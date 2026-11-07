import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from db import get_db
import models
import schemas
from deps import require_authenticated_user_id

router = APIRouter(prefix="/api/v1/clients", tags=["Clients"])

@router.post("", response_model=schemas.ClientResponse)
async def create_client(
    client_data: schemas.ClientCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    new_client = models.Client(
        id=str(uuid.uuid4()),
        user_id=current_user_id,
        name=client_data.name,
        phone=client_data.phone,
        email=client_data.email,
        address=client_data.address
    )
    db.add(new_client)
    await db.flush()
    return new_client

@router.get("", response_model=List[schemas.ClientResponse])
async def get_clients(
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = select(models.Client).where(models.Client.user_id == current_user_id)
    result = await db.execute(query.order_by(models.Client.created_at.desc()).limit(limit).offset(offset))
    return result.scalars().all()

@router.get("/{client_id}", response_model=schemas.ClientResponse)
async def get_client(
    client_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = select(models.Client).where(models.Client.id == client_id, models.Client.user_id == current_user_id)
    result = await db.execute(query)
    client = result.scalars().first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client

@router.put("/{client_id}", response_model=schemas.ClientResponse)
async def update_client(
    client_id: str,
    client_data: schemas.ClientUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = select(models.Client).where(models.Client.id == client_id, models.Client.user_id == current_user_id)
    result = await db.execute(query)
    client = result.scalars().first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
        
    if client_data.name is not None:
        client.name = client_data.name
    if client_data.phone is not None:
        client.phone = client_data.phone
    if client_data.email is not None:
        client.email = client_data.email
    if client_data.address is not None:
        client.address = client_data.address
        
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client

@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = select(models.Client).where(models.Client.id == client_id, models.Client.user_id == current_user_id)
    result = await db.execute(query)
    client = result.scalars().first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
        
    await db.delete(client)
    await db.commit()
    return None

@router.get("/{client_id}/payments", response_model=List[schemas.PaymentResponse])
async def get_client_payments(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = (
        select(models.Payment, models.Project.name.label("project_name"))
        .join(models.Project, models.Payment.project_id == models.Project.id)
        .where(models.Project.client_id == client_id, models.Project.user_id == current_user_id)
        .order_by(desc(models.Payment.payment_date))
    )
    
    pay_res = await db.execute(query)
    results = []
    for row in pay_res.all():
        pay, p_name = row[0], row[1]
        pay_dict = {
            "id": pay.id,
            "project_id": pay.project_id,
            "project_name": p_name,
            "amount": pay.amount,
            "payment_date": pay.payment_date,
            "payment_method": pay.payment_method,
            "transaction_id": pay.transaction_id,
            "notes": pay.notes,
            "created_at": pay.created_at
        }
        results.append(pay_dict)
    return results
