from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from db import get_db
import models
import schemas
from deps import (
    get_current_user,
    require_authenticated_user_id,
    hash_password,
    clear_cached_token_version,
)

router = APIRouter(prefix="/api/v1", tags=["User Profile & Management"])

@router.get("/auth/me", response_model=schemas.UserResponse)
async def get_me(user: models.User = Depends(get_current_user)):
    return user

@router.put("/auth/profile", response_model=schemas.UserResponse)
async def update_profile(
    profile_data: schemas.UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    result = await db.execute(select(models.User).where(models.User.id == current_user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if profile_data.email and profile_data.email != user.email:
        email_check = await db.execute(select(models.User).where(models.User.email == profile_data.email))
        if email_check.scalars().first():
            raise HTTPException(status_code=400, detail="Email is already in use by another account")
        user.email = profile_data.email
        
    if profile_data.name is not None:
        user.name = profile_data.name
    if profile_data.phone is not None:
        user.phone = profile_data.phone
    if profile_data.company_logo is not None:
        user.company_logo = profile_data.company_logo
    if profile_data.general_terms is not None:
        user.general_terms = profile_data.general_terms
    if profile_data.billing_terms is not None:
        user.billing_terms = profile_data.billing_terms
    if profile_data.signature is not None:
        user.signature = profile_data.signature
    if profile_data.stamp is not None:
        user.stamp = profile_data.stamp
    if profile_data.billing_template is not None:
        user.billing_template = profile_data.billing_template
        
    password_changed = False
    if profile_data.new_password:
        user.password_hash = hash_password(profile_data.new_password)
        user.token_version = (user.token_version or 0) + 1  # password change signs out all sessions
        password_changed = True
        
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if password_changed:
        clear_cached_token_version(user.id)
    return user


@router.put("/auth/tour", response_model=schemas.UserResponse)
async def update_tour_status(
    payload: schemas.TourStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id),
):
    """Mark the onboarding tour as seen (finished or skipped)."""
    user = (await db.execute(select(models.User).where(models.User.id == current_user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.tour_completed = payload.completed
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# --- ADMIN USER MANAGEMENT ENDPOINTS ---
async def _require_admin(db: AsyncSession, user_id: str) -> None:
    """Admin check that reads only role + is_approved (not the whole user row incl. logo)."""
    row = (await db.execute(
        select(models.User.role, models.User.is_approved).where(models.User.id == user_id)
    )).first()
    if not row or row[0] != "Admin" or not row[1]:
        raise HTTPException(status_code=403, detail="Admin access required")

@router.get("/users", response_model=List[schemas.UserListItem])
async def get_all_users(
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    await _require_admin(db, current_user_id)
    U, S = models.User, models.UserSubscription
    result = await db.execute(
        select(U.id, U.name, U.email, U.role, U.phone, U.is_approved, U.created_at,
               S.access_start, S.access_end,
               func.coalesce(S.payment_status, "unpaid").label("payment_status"),
               func.coalesce(S.selected_plan, "monthly").label("selected_plan"),
               func.coalesce(S.status, "active").label("status"))
        .outerjoin(S, S.user_id == U.id)
        .order_by(desc(U.created_at))
    )
    return [dict(r._mapping) for r in result.all()]

@router.get("/users/{user_id}", response_model=schemas.UserResponse)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    """Full record (incl. logo and terms) for the admin edit panel."""
    await _require_admin(db, current_user_id)
    user = (await db.execute(select(models.User).where(models.User.id == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.put("/users/{user_id}/approve", response_model=schemas.UserResponse)
async def approve_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    await _require_admin(db, current_user_id)

    user_res = await db.execute(select(models.User).where(models.User.id == user_id))
    user = user_res.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    user.is_approved = True
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.put("/users/{user_id}/role", response_model=schemas.UserResponse)
async def update_user_role(
    user_id: str,
    role_data: schemas.UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    await _require_admin(db, current_user_id)

    user_res = await db.execute(select(models.User).where(models.User.id == user_id))
    user = user_res.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user_id:
        if role_data.role != user.role:
            raise HTTPException(status_code=400, detail="Cannot change your own role")
        if role_data.access_start is not None or role_data.access_end is not None:
            raise HTTPException(status_code=400, detail="Cannot change your own access dates")

    if role_data.email and role_data.email != user.email:
        email_check = await db.execute(select(models.User).where(models.User.email == role_data.email))
        if email_check.scalars().first():
            raise HTTPException(status_code=400, detail="Email is already registered by another account")
        user.email = role_data.email

    if role_data.name is not None:
        user.name = role_data.name
    if role_data.phone is not None:
        user.phone = role_data.phone
    if role_data.company_logo is not None:
        user.company_logo = role_data.company_logo
    if role_data.general_terms is not None:
        user.general_terms = role_data.general_terms

    if user.id != current_user_id:
        user.role = role_data.role
        user.access_start = role_data.access_start.replace(tzinfo=None) if role_data.access_start is not None else None
        user.access_end = role_data.access_end.replace(tzinfo=None) if role_data.access_end is not None else None
        # Granting a future end date reactivates an expired / deactivated account.
        if user.access_end is not None and user.access_end > datetime.utcnow():
            user.status = "active"

    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.put("/users/{user_id}/status", response_model=schemas.UserResponse)
async def update_user_status(
    user_id: str,
    body: schemas.UserStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    await _require_admin(db, current_user_id)
    if user_id == current_user_id:
        raise HTTPException(status_code=400, detail="Cannot change your own status")

    user = (await db.execute(select(models.User).where(models.User.id == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.status = body.status
    if body.status == "deactivated":
        user.token_version = (user.token_version or 0) + 1  # sign the user out everywhere
    db.add(user)
    await db.commit()
    await db.refresh(user)
    clear_cached_token_version(user.id)
    return user

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    await _require_admin(db, current_user_id)

    user_res = await db.execute(select(models.User).where(models.User.id == user_id))
    user = user_res.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user.id == current_user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account")
        
    await db.delete(user)
    await db.commit()
    return {"message": "User deleted successfully"}

@router.get("/users/{user_id}/subscriptions", response_model=List[schemas.SubscriptionPaymentResponse])
async def get_user_subscriptions(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    await _require_admin(db, current_user_id)

    result = await db.execute(
        select(models.SubscriptionPayment)
        .where(models.SubscriptionPayment.user_id == user_id)
        .order_by(desc(models.SubscriptionPayment.created_at))
    )
    return result.scalars().all()
