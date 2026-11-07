import uuid
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Response, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from db import get_db
import models
import schemas
from deps import require_authenticated_user_id

router = APIRouter(prefix="/api/v1", tags=["Payment Tracking"])

@router.post("/payments", response_model=schemas.PaymentResponse)
async def create_payment(
    payment_data: schemas.PaymentCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    proj_query = select(models.Project).where(models.Project.id == payment_data.project_id, models.Project.user_id == current_user_id)
    proj_res = await db.execute(proj_query)
    proj = proj_res.scalars().first()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    p_date = payment_data.payment_date.replace(tzinfo=None) if payment_data.payment_date else datetime.utcnow()
    new_payment = models.Payment(
        id=str(uuid.uuid4()),
        project_id=payment_data.project_id,
        amount=payment_data.amount,
        payment_date=p_date,
        payment_method=payment_data.payment_method,
        transaction_id=payment_data.transaction_id,
        notes=payment_data.notes
    )
    db.add(new_payment)
    await db.commit()
    await db.refresh(new_payment)
    return new_payment

@router.get("/projects/{project_id}/payments", response_model=List[schemas.PaymentResponse])
async def get_project_payments(
    project_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    proj_query = select(models.Project).where(models.Project.id == project_id, models.Project.user_id == current_user_id)
    proj_res = await db.execute(proj_query)
    proj = proj_res.scalars().first()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    pay_res = await db.execute(
        select(models.Payment)
        .where(models.Payment.project_id == project_id)
        .order_by(desc(models.Payment.payment_date))
    )
    return pay_res.scalars().all()

@router.get("/payments", response_model=List[schemas.PaymentResponse])
async def get_all_payments(
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = (
        select(models.Payment)
        .join(models.Project, models.Payment.project_id == models.Project.id)
        .where(models.Project.user_id == current_user_id)
        .order_by(desc(models.Payment.payment_date))
        .limit(limit).offset(offset)
    )
    pay_res = await db.execute(query)
    return pay_res.scalars().all()

@router.delete("/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_payment(
    payment_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = (
        select(models.Payment)
        .join(models.Project, models.Payment.project_id == models.Project.id)
        .where(models.Payment.id == payment_id, models.Project.user_id == current_user_id)
    )
    pay_res = await db.execute(query)
    payment = pay_res.scalars().first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found")
    
    await db.delete(payment)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
