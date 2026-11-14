from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from db import get_db
import models
import schemas
from deps import require_admin

router = APIRouter(prefix="/api/v1/admin/coupons", tags=["Coupons (Admin)"])


def _apply(coupon: models.Coupon, data: schemas.CouponIn) -> None:
    coupon.discount_percent = data.discount_percent
    coupon.free_days = data.free_days
    coupon.valid_from = data.valid_from
    coupon.valid_until = data.valid_until
    coupon.max_redemptions = data.max_redemptions
    coupon.is_active = data.is_active


@router.get("", response_model=List[schemas.CouponOut])
async def list_coupons(_: str = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(models.Coupon).order_by(models.Coupon.created_at.desc()))
    return rows.scalars().all()


@router.post("", response_model=schemas.CouponOut, status_code=201)
async def create_coupon(payload: schemas.CouponIn, _: str = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    code = payload.code.strip().upper()
    if (await db.execute(select(models.Coupon).where(models.Coupon.code == code))).scalars().first():
        raise HTTPException(status_code=400, detail="A coupon with this code already exists.")
    coupon = models.Coupon(code=code, redeemed_count=0)
    _apply(coupon, payload)
    db.add(coupon)
    await db.commit()
    await db.refresh(coupon)
    return coupon


@router.put("/{code}", response_model=schemas.CouponOut)
async def update_coupon(code: str, payload: schemas.CouponIn, _: str = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    coupon = (await db.execute(
        select(models.Coupon).where(models.Coupon.code == code.strip().upper()).with_for_update()
    )).scalars().first()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found.")
    _apply(coupon, payload)  # the code itself cannot be renamed
    await db.commit()
    await db.refresh(coupon)
    return coupon


@router.delete("/{code}", status_code=204)
async def delete_coupon(code: str, _: str = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    coupon = (await db.execute(
        select(models.Coupon).where(models.Coupon.code == code.strip().upper()).with_for_update()
    )).scalars().first()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found.")
    if (coupon.redeemed_count or 0) > 0:
        raise HTTPException(status_code=409, detail="This coupon has been used, so it can't be deleted. Deactivate it instead.")
    await db.delete(coupon)
    await db.commit()
