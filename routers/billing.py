"""Billing: bills, their items, generation (lock) and PDF download.

Security notes
- Every query is scoped to the authenticated user; foreign ids answer 404 (never 403).
- Totals are always recomputed here from the items; client-supplied amounts are not accepted.
- Only draft bills can be edited or deleted; a generated bill is a frozen document.
"""
import re
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload
from starlette.concurrency import run_in_threadpool

import models
import schemas
from bill_pdf import generate_bill_pdf
from db import get_db
from deps import require_authenticated_user_id
from pdf_generator import sanitize_logo
from ratelimit import rate_limit

router = APIRouter(prefix="/api/v1", tags=["Billing"])

_CENT = Decimal("0.01")
_PAID_EPSILON = 0.005


def _d(value) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> float:
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def measure(it) -> Decimal:
    """Units billed per qty, using the estimation rules: piece = 1, running ft = length,
    sq ft = length x (height, else breadth, else 1)."""
    if it.pricing_type == "running_ft":
        return _d(it.length)
    if it.pricing_type == "sq_ft":
        second = _d(it.height) if it.height > 0 else (_d(it.breadth) if it.breadth > 0 else Decimal("1"))
        return _d(it.length) * second
    return Decimal("1")


def compute_totals(items: Iterable[schemas.BillItemIn], discount: float):
    """Return (per-item amounts, subtotal, tax, grand_total). Rounded to paise per item."""
    amounts: List[float] = []
    subtotal = tax = Decimal("0")
    for n, it in enumerate(items, 1):
        if it.pricing_type != "piece" and it.length <= 0:
            raise HTTPException(status_code=422, detail=f"Item {n}: length must be more than 0")
        base = (_d(it.qty) * measure(it) * _d(it.rate)).quantize(_CENT, rounding=ROUND_HALF_UP)
        line_tax = (base * _d(it.tax_percent) / 100).quantize(_CENT, rounding=ROUND_HALF_UP)
        subtotal += base
        tax += line_tax
        amounts.append(_money(base + line_tax))
    discount_d = _d(discount)
    if discount_d > subtotal + tax:
        raise HTTPException(status_code=422, detail="Discount cannot be more than the bill total")
    return amounts, _money(subtotal), _money(tax), _money(subtotal + tax - discount_d)


def payment_state(grand_total: float, paid: float) -> str:
    if paid <= _PAID_EPSILON:
        return "unpaid"
    return "paid" if paid + _PAID_EPSILON >= grand_total else "partial"


async def _paid_by_bill(db: AsyncSession, bill_ids: List[str]) -> Dict[str, float]:
    if not bill_ids:
        return {}
    rows = await db.execute(
        select(models.Payment.bill_id, func.coalesce(func.sum(models.Payment.amount), 0.0))
        .where(models.Payment.bill_id.in_(bill_ids))
        .group_by(models.Payment.bill_id)
    )
    return {bill_id: float(total) for bill_id, total in rows.all()}


def _response(bill: models.Bill, client_name: Optional[str], project_name: Optional[str],
              paid: float, with_items: bool) -> schemas.BillResponse:
    return schemas.BillResponse(
        id=bill.id, bill_number=bill.bill_number,
        client_id=bill.client_id, client_name=client_name,
        project_id=bill.project_id, project_name=project_name,
        bill_date=bill.bill_date, due_date=bill.due_date, status=bill.status,
        template=bill.template, customer_gstin=bill.customer_gstin, notes=bill.notes,
        subtotal=bill.subtotal, tax_amount=bill.tax_amount,
        discount_amount=bill.discount_amount, grand_total=bill.grand_total,
        paid_amount=paid, balance=max(0.0, round(bill.grand_total - paid, 2)),
        payment_status=payment_state(bill.grand_total, paid),
        created_at=bill.created_at,
        items=[schemas.BillItemOut.model_validate(i) for i in bill.items] if with_items else [],
    )


async def _owned_bill(db: AsyncSession, bill_id: str, user_id: str, lock: bool = False) -> models.Bill:
    query = select(models.Bill).where(models.Bill.id == bill_id, models.Bill.user_id == user_id)
    if lock:
        query = query.with_for_update()
    bill = (await db.execute(query)).scalars().first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    return bill


async def _owned_client_project(db: AsyncSession, user_id: str, client_id: str, project_id: str):
    row = (await db.execute(
        select(models.Client.name, models.Project.name)
        .join(models.Project, models.Project.client_id == models.Client.id)
        .where(
            models.Client.id == client_id, models.Client.user_id == user_id,
            models.Project.id == project_id, models.Project.user_id == user_id,
        )
    )).first()
    if not row:
        raise HTTPException(status_code=404, detail="Client or project not found")
    return row[0], row[1]


def _fill_items(bill: models.Bill, items: List[schemas.BillItemIn], amounts: List[float]) -> None:
    bill.items = [
        models.BillItem(
            id=str(uuid.uuid4()), position=i, description=it.description.strip(),
            qty=it.qty, rate=it.rate, tax_percent=it.tax_percent, amount=amounts[i],
            pricing_type=it.pricing_type, length=it.length, breadth=it.breadth, height=it.height,
            remark=(it.remark or "").strip() or None,
        )
        for i, it in enumerate(items)
    ]


async def _next_bill_number(db: AsyncSession, user_id: str, year: int) -> str:
    prefix = f"BILL-{year}-"
    last = (await db.execute(
        select(func.max(models.Bill.bill_number)).where(
            models.Bill.user_id == user_id, models.Bill.bill_number.like(prefix + "%")
        )
    )).scalar()
    seq = int(last.rsplit("-", 1)[1]) + 1 if last and last.rsplit("-", 1)[1].isdigit() else 1
    return f"{prefix}{seq:04d}"


@router.post("/bills", response_model=schemas.BillResponse, status_code=status.HTTP_201_CREATED)
async def create_bill(
    data: schemas.BillWrite,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_authenticated_user_id),
):
    client_name, project_name = await _owned_client_project(db, user_id, data.client_id, data.project_id)
    amounts, subtotal, tax, grand = compute_totals(data.items, data.discount_amount)
    template = (await db.execute(
        select(models.UserProfile.billing_template).where(models.UserProfile.user_id == user_id)
    )).scalar() or "modern"

    # Number allocation can race with a concurrent request: retry on the unique constraint.
    for _ in range(3):
        bill = models.Bill(
            id=str(uuid.uuid4()), user_id=user_id, client_id=data.client_id, project_id=data.project_id,
            bill_number=await _next_bill_number(db, user_id, data.bill_date.year),
            bill_date=data.bill_date, due_date=data.due_date, status="draft", template=template,
            customer_gstin=data.customer_gstin or None, notes=data.notes or None,
            subtotal=subtotal, tax_amount=tax, discount_amount=data.discount_amount, grand_total=grand,
        )
        _fill_items(bill, data.items, amounts)
        try:
            async with db.begin_nested():
                db.add(bill)
                await db.flush()
            break
        except IntegrityError:
            db.expunge(bill)
    else:
        raise HTTPException(status_code=409, detail="Could not allocate a bill number. Try again.")
    return _response(bill, client_name, project_name, 0.0, with_items=True)


@router.get("/bills", response_model=List[schemas.BillResponse])
async def list_bills(
    project_id: Optional[str] = Query(None, max_length=36),
    bill_status: Optional[str] = Query(None, alias="status", pattern="^(draft|generated)$"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_authenticated_user_id),
):
    query = (
        select(models.Bill, models.Client.name, models.Project.name)
        .join(models.Client, models.Client.id == models.Bill.client_id)
        .join(models.Project, models.Project.id == models.Bill.project_id)
        .options(noload(models.Bill.items))
        .where(models.Bill.user_id == user_id)
        .order_by(desc(models.Bill.created_at))
        .limit(limit).offset(offset)
    )
    if project_id:
        query = query.where(models.Bill.project_id == project_id)
    if bill_status:
        query = query.where(models.Bill.status == bill_status)
    rows = (await db.execute(query)).all()
    paid = await _paid_by_bill(db, [bill.id for bill, _, _ in rows])
    return [_response(bill, c, p, paid.get(bill.id, 0.0), with_items=False) for bill, c, p in rows]


async def _detail(db: AsyncSession, bill: models.Bill) -> schemas.BillResponse:
    names = (await db.execute(
        select(models.Client.name, models.Project.name)
        .join(models.Project, models.Project.client_id == models.Client.id)
        .where(models.Client.id == bill.client_id, models.Project.id == bill.project_id)
    )).first() or (None, None)
    paid = (await _paid_by_bill(db, [bill.id])).get(bill.id, 0.0)
    return _response(bill, names[0], names[1], paid, with_items=True)


@router.get("/bills/{bill_id}", response_model=schemas.BillResponse)
async def get_bill(
    bill_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_authenticated_user_id),
):
    return await _detail(db, await _owned_bill(db, bill_id, user_id))


@router.put("/bills/{bill_id}", response_model=schemas.BillResponse)
async def update_bill(
    bill_id: str,
    data: schemas.BillWrite,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_authenticated_user_id),
):
    bill = await _owned_bill(db, bill_id, user_id, lock=True)
    if bill.status != "draft":
        raise HTTPException(status_code=409, detail="A generated bill can no longer be edited")
    await _owned_client_project(db, user_id, data.client_id, data.project_id)
    amounts, subtotal, tax, grand = compute_totals(data.items, data.discount_amount)
    bill.client_id, bill.project_id = data.client_id, data.project_id
    bill.bill_date, bill.due_date = data.bill_date, data.due_date
    bill.customer_gstin, bill.notes = data.customer_gstin or None, data.notes or None
    bill.subtotal, bill.tax_amount, bill.discount_amount, bill.grand_total = subtotal, tax, data.discount_amount, grand
    _fill_items(bill, data.items, amounts)
    await db.flush()
    return await _detail(db, bill)


@router.delete("/bills/{bill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bill(
    bill_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_authenticated_user_id),
):
    bill = await _owned_bill(db, bill_id, user_id, lock=True)
    if bill.status != "draft":
        raise HTTPException(status_code=409, detail="A generated bill cannot be deleted")
    await db.delete(bill)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/bills/{bill_id}/generate", response_model=schemas.BillResponse)
async def generate_bill(
    bill_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_authenticated_user_id),
):
    bill = await _owned_bill(db, bill_id, user_id, lock=True)
    if bill.status == "draft":
        bill.template = (await db.execute(
            select(models.UserProfile.billing_template).where(models.UserProfile.user_id == user_id)
        )).scalar() or "modern"
        bill.status = "generated"
        await db.flush()
    return await _detail(db, bill)


@router.get("/bills/{bill_id}/payments", response_model=List[schemas.PaymentResponse])
async def get_bill_payments(
    bill_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_authenticated_user_id),
):
    await _owned_bill(db, bill_id, user_id)
    rows = await db.execute(
        select(models.Payment).where(models.Payment.bill_id == bill_id).order_by(desc(models.Payment.payment_date))
    )
    return rows.scalars().all()


@router.get("/bills/{bill_id}/pdf", dependencies=[Depends(rate_limit("pdf", 30, 60))])
async def download_bill_pdf(
    bill_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_authenticated_user_id),
):
    bill = await _owned_bill(db, bill_id, user_id)
    detail = await _detail(db, bill)
    client = (await db.execute(select(models.Client).where(models.Client.id == bill.client_id))).scalars().first()
    user = (await db.execute(select(models.User).where(models.User.id == user_id))).scalars().first()
    payments = (await db.execute(
        select(models.Payment).where(models.Payment.bill_id == bill_id).order_by(models.Payment.payment_date)
    )).scalars().all()

    context = {
        # Always the profile's current template, so changing it restyles bills already generated too.
        "template": user.billing_template if user else "modern",
        "bill": detail.model_dump(),
        "customer": {
            "name": client.name if client else detail.client_name,
            "phone": client.phone if client else None,
            "email": client.email if client else None,
            "address": client.address if client else None,
            "gstin": bill.customer_gstin,
        },
        "company": {
            "name": user.name if user else "",
            "email": user.email if user else None,
            "phone": user.phone if user else None,
            "logo": sanitize_logo(user.company_logo) if user else None,
            "terms": user.billing_terms if user else None,
            "signature": sanitize_logo(user.signature) if user else None,
            "stamp": sanitize_logo(user.stamp) if user else None,
        },
        "payments": [
            {"date": p.payment_date.date(), "method": p.payment_method, "amount": p.amount, "reference": p.transaction_id}
            for p in payments
        ],
    }
    pdf_bytes = await run_in_threadpool(generate_bill_pdf, context)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", detail.client_name or "Customer").strip("_")[:60] or "Customer"
    filename = f"Bill_{safe_name}_{bill.bill_date.isoformat()}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
