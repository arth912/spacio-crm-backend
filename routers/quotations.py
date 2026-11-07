import re
import uuid
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from db import get_db
import models
import schemas
from pricing import calculate_item_price
from pdf_generator import generate_quotation_pdf
from deps import require_authenticated_user_id
from ratelimit import rate_limit
from pdf_generator import sanitize_logo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Quotations Engine"])

async def get_quotation_details(quotation_id: str, db: AsyncSession, owner_id: Optional[str] = None) -> schemas.QuotationResponse:
    q_res = await db.execute(select(models.Quotation).where(models.Quotation.id == quotation_id))
    q = q_res.scalars().first()
    if not q:
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    p_res = await db.execute(select(models.Project).where(models.Project.id == q.project_id))
    project = p_res.scalars().first()
    if owner_id is not None and (not project or project.user_id != owner_id):
        raise HTTPException(status_code=404, detail="Quotation not found")
    client_name = ""
    project_name = ""
    site_address = ""
    if project:
        project_name = project.name
        site_address = project.site_address
        c_res = await db.execute(select(models.Client).where(models.Client.id == project.client_id))
        client = c_res.scalars().first()
        if client:
            client_name = client.name
            
    items_res = await db.execute(
        select(
            models.QuotationItem,
            models.Room.room_name,
            models.Category.name.label("category_name"),
            models.Item.name.label("item_name"),
            models.Item.brand.label("item_brand"),
            models.Item.material.label("item_material"),
            models.Item.dimensions.label("item_dimensions"),
            models.Item.pricing_type.label("item_pricing_type")
        )
        .join(models.Room, models.QuotationItem.room_id == models.Room.id)
        .join(models.Category, models.QuotationItem.category_id == models.Category.id)
        .join(models.Item, models.QuotationItem.item_id == models.Item.id)
        .where(models.QuotationItem.quotation_id == quotation_id)
    )
    
    items_out = []
    for row in items_res.all():
        qi, room_name, cat_name, item_name, item_brand, material, dims, item_pricing_type = row
        mapped_pricing_type = qi.pricing_type if (qi.pricing_type is not None and qi.pricing_type != "") else item_pricing_type
        items_out.append(
            schemas.QuotationItemResponse(
                id=qi.id,
                room_id=qi.room_id,
                room_name=room_name,
                category_id=qi.category_id,
                category_name=cat_name,
                item_id=qi.item_id,
                item_name=item_name,
                item_brand=item_brand,
                pricing_type=mapped_pricing_type,
                qty=qi.qty,
                remark=qi.remark,
                length=qi.length,
                breadth=qi.breadth,
                height=qi.height,
                snapshot_rate=qi.snapshot_rate,
                snapshot_labor_cost=qi.snapshot_labor_cost,
                snapshot_margin=qi.snapshot_margin,
                snapshot_gst_percent=qi.snapshot_gst_percent,
                final_price=qi.final_price,
                total_amount=qi.total_amount,
                created_at=qi.created_at,
                item_material=material,
                item_dimensions=dims
            )
        )
        
    res = schemas.QuotationResponse(
        id=q.id,
        project_id=q.project_id,
        project_name=project_name,
        client_name=client_name,
        site_address=site_address,
        version=q.version,
        subtotal=q.subtotal,
        gst_amount=q.gst_amount,
        discount_amount=q.discount_amount,
        grand_total=q.grand_total,
        status=q.status,
        terms_conditions=q.terms_conditions,
        created_at=q.created_at,
        items=items_out
    )
    return res

@router.post("/quotations", response_model=schemas.QuotationResponse)
async def create_quotation(
    q_data: schemas.QuotationCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    p_query = select(models.Project).where(models.Project.id == q_data.project_id, models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    project = p_res.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    v_res = await db.execute(
        select(models.Quotation)
        .where(models.Quotation.project_id == q_data.project_id)
        .order_by(desc(models.Quotation.version))
    )
    latest_quote = v_res.scalars().first()
    version = 1
    
    if latest_quote:
        version = latest_quote.version + 1
        latest_quote.status = "revised"
        db.add(latest_quote)
        
    quotation_id = str(uuid.uuid4())
    new_quote = models.Quotation(
        id=quotation_id,
        project_id=q_data.project_id,
        version=version,
        subtotal=0.0,
        gst_amount=0.0,
        discount_amount=q_data.discount_amount,
        grand_total=0.0,
        status="draft",
        terms_conditions=q_data.terms_conditions
    )
    db.add(new_quote)
    await db.flush()
    
    subtotal_aggregate = 0.0
    
    item_ids = list(set([item_in.item_id for item_in in q_data.items]))
    master_items_map = {}
    if item_ids:
        items_res = await db.execute(select(models.Item).where(
            models.Item.id.in_(item_ids),
            models.Item.user_id == current_user_id,
        ))
        master_items_map = {it.id: it for it in items_res.scalars().all()}
    
    room_ids = {i.room_id for i in q_data.items}
    if room_ids:
        rooms_res = await db.execute(
            select(models.Room.id).where(models.Room.id.in_(room_ids), models.Room.project_id == q_data.project_id)
        )
        if {r[0] for r in rooms_res.all()} != room_ids:
            raise HTTPException(status_code=400, detail="One or more rooms do not belong to this project")

    for item_in in q_data.items:
        master_item = master_items_map.get(item_in.item_id)
        if not master_item:
            raise HTTPException(status_code=404, detail=f"Master item ID {item_in.item_id} not found in catalog")
        if item_in.category_id != master_item.category_id:
            raise HTTPException(status_code=400, detail="Item does not belong to the given category")
            
        ptype = item_in.pricing_type if (item_in.pricing_type is not None and item_in.pricing_type != "") else master_item.pricing_type
        brate = item_in.base_rate if item_in.base_rate is not None else master_item.base_rate
        lcost = item_in.labor_cost if item_in.labor_cost is not None else master_item.labor_cost

        pricing_breakdown = calculate_item_price(
            base_rate=brate,
            qty=item_in.qty,
            labor_cost=lcost,
            margin_percent=item_in.margin_percent,
            gst_percent=item_in.gst_percent,
            pricing_type=ptype,
            length=item_in.length if item_in.length is not None else 1.0,
            breadth=item_in.breadth if item_in.breadth is not None else 1.0,
            height=item_in.height if item_in.height is not None else 1.0
        )
        
        qi_id = str(uuid.uuid4())
        new_q_item = models.QuotationItem(
            id=qi_id,
            quotation_id=quotation_id,
            room_id=item_in.room_id,
            category_id=item_in.category_id,
            item_id=item_in.item_id,
            qty=item_in.qty,
            remark=item_in.remark,
            length=item_in.length if item_in.length is not None else 1.0,
            breadth=item_in.breadth if item_in.breadth is not None else 1.0,
            height=item_in.height if item_in.height is not None else 1.0,
            pricing_type=ptype,
            snapshot_rate=brate,
            snapshot_labor_cost=lcost,
            snapshot_margin=item_in.margin_percent,
            snapshot_gst_percent=item_in.gst_percent,
            final_price=pricing_breakdown["final_unit_price"],
            total_amount=pricing_breakdown["total_amount"]
        )
        db.add(new_q_item)
        
        subtotal_aggregate += pricing_breakdown["total_amount"]
        
    global_gst = 0.0
    if q_data.apply_global_gst and q_data.global_gst_percent:
        global_gst = subtotal_aggregate * (q_data.global_gst_percent / 100.0)
        
    grand_total = subtotal_aggregate + global_gst - q_data.discount_amount
    if grand_total < 0:
        grand_total = 0.0
        
    new_quote.subtotal = round(subtotal_aggregate, 2)
    new_quote.gst_amount = round(global_gst, 2)
    new_quote.grand_total = round(grand_total, 2)
    
    db.add(new_quote)
    await db.commit()
    
    return await get_quotation_details(quotation_id, db, current_user_id)

def _summary_query():
    """Quotation + project + client in ONE query (no N+1, no line items)."""
    return (
        select(
            models.Quotation,
            models.Project.name.label("project_name"),
            models.Project.site_address.label("site_address"),
            models.Client.name.label("client_name"),
        )
        .join(models.Project, models.Quotation.project_id == models.Project.id)
        .outerjoin(models.Client, models.Project.client_id == models.Client.id)
    )

def _to_summary(row) -> schemas.QuotationResponse:
    q, project_name, site_address, client_name = row
    return schemas.QuotationResponse(
        id=q.id,
        project_id=q.project_id,
        project_name=project_name or "",
        client_name=client_name or "",
        site_address=site_address or "",
        version=q.version,
        subtotal=q.subtotal,
        gst_amount=q.gst_amount,
        discount_amount=q.discount_amount,
        grand_total=q.grand_total,
        status=q.status,
        terms_conditions=None,
        created_at=q.created_at,
        items=[],  # line items are served by GET /quotations/{id}
    )

@router.get("/quotations", response_model=List[schemas.QuotationResponse])
async def get_all_user_quotations(
    response: Response,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    """Lightweight list (summary only). Fetch a single quotation for its line items."""
    total = await db.scalar(
        select(func.count(models.Quotation.id))
        .join(models.Project, models.Quotation.project_id == models.Project.id)
        .where(models.Project.user_id == current_user_id)
    )
    res = await db.execute(
        _summary_query()
        .where(models.Project.user_id == current_user_id)
        .order_by(desc(models.Quotation.created_at))
        .limit(limit).offset(offset)
    )
    response.headers["X-Total-Count"] = str(total or 0)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return [_to_summary(r) for r in res.all()]

@router.get("/quotations/{quotation_id}", response_model=schemas.QuotationResponse)
async def get_quotation(
    quotation_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    q_res = await db.execute(select(models.Quotation).where(models.Quotation.id == quotation_id))
    q = q_res.scalars().first()
    if not q:
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    p_query = select(models.Project).where(models.Project.id == q.project_id, models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Quotation not found")

    return await get_quotation_details(quotation_id, db, current_user_id)

@router.get("/projects/{project_id}/quotations", response_model=List[schemas.QuotationResponse])
async def get_project_quotations(
    project_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    p_query = select(models.Project).where(models.Project.id == project_id, models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Project not found")

    res = await db.execute(
        _summary_query()
        .where(models.Quotation.project_id == project_id)
        .order_by(desc(models.Quotation.version))
    )
    return [_to_summary(r) for r in res.all()]

@router.put("/quotations/{quotation_id}/status", response_model=schemas.QuotationResponse)
async def update_quotation_status(
    quotation_id: str, 
    status_data: schemas.QuotationStatusUpdate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    q_res = await db.execute(select(models.Quotation).where(models.Quotation.id == quotation_id))
    q = q_res.scalars().first()
    if not q:
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    p_query = select(models.Project).where(models.Project.id == q.project_id, models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    q.status = status_data.status
    db.add(q)
    await db.commit()
    return await get_quotation_details(quotation_id, db, current_user_id)

@router.get("/quotations/{quotation_id}/pdf", dependencies=[Depends(rate_limit("pdf", 30, 60))])
async def download_quotation_pdf(
    quotation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    q_res = await db.execute(select(models.Quotation).where(models.Quotation.id == quotation_id))
    q = q_res.scalars().first()
    if not q:
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    p_query = select(models.Project).where(
        models.Project.id == q.project_id,
        models.Project.user_id == current_user_id,
    )
    p_res = await db.execute(p_query)
    project = p_res.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Quotation not found")

    creator_dict = None
    if project.user_id:
        user_res = await db.execute(select(models.User).where(models.User.id == project.user_id))
        user = user_res.scalars().first()
        if user:
            creator_dict = {
                "name": user.name,
                "email": user.email,
                "phone": user.phone,
                "company_logo": sanitize_logo(user.company_logo),
                "general_terms": user.general_terms
            }

    q_details = await get_quotation_details(quotation_id, db)
    q_dict = q_details.dict()
    q_dict["creator"] = creator_dict
    
    q_dict["created_at"] = q_dict["created_at"].strftime("%Y-%m-%d %H:%M")
    for item in q_dict.get("items", []):
        item["created_at"] = item["created_at"].strftime("%Y-%m-%d %H:%M")
        
    from starlette.concurrency import run_in_threadpool
    pdf_bytes = await run_in_threadpool(generate_quotation_pdf, q_dict)
    safe_client = re.sub(r"[^A-Za-z0-9_-]+", "_", q_details.client_name or "Client").strip("_")[:60] or "Client"
    filename = f"Proposal_{safe_client}_V{q_details.version}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )
