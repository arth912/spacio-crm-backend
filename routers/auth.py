import uuid
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Header
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import load_only, lazyload, selectinload

from db import get_db
import models
import schemas
from deps import (
    hash_password,
    verify_password,
    create_access_token,
    RAZORPAY_KEY_ID,
    razor_client,
    generate_otp,
    hash_otp,
    otp_matches,
    OTP_MAX_ATTEMPTS,
    require_authenticated_user_id,
    set_cached_token_version,
    clear_cached_token_version,
)
from ratelimit import rate_limit
from email_utils import (
    send_email,
    get_verification_html,
    get_reset_password_html,
    get_payment_confirmation_html
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

def _tokens(user: models.User) -> models.UserAuthTokens:
    """OTP/reset state row for a user (creates it if missing). Requires auth_tokens to be eager-loaded."""
    if user.auth_tokens is None:
        user.auth_tokens = models.UserAuthTokens()
    return user.auth_tokens

_WITH_TOKENS = selectinload(models.User.auth_tokens)

@router.post("/register", response_model=schemas.RegisterResponse, dependencies=[Depends(rate_limit("register", 5, 600))])
async def register(user_data: schemas.UserRegister, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).options(_WITH_TOKENS).where(models.User.email == user_data.email))
    existing_user = result.scalars().first()
    
    hashed = await run_in_threadpool(hash_password, user_data.password)
    code = generate_otp()
    code_hash = hash_otp(code)
    expires = datetime.utcnow() + timedelta(minutes=15)
    # Public self-registration can never create an Admin
    safe_role = "Sales" if user_data.role == "Sales" else "Designer"
    
    if existing_user:
        if existing_user.is_email_verified:
            raise HTTPException(status_code=400, detail="Email is already registered")
        else:
            existing_user.name = user_data.name
            existing_user.password_hash = hashed
            existing_user.phone = user_data.phone
            existing_user.company_logo = user_data.company_logo
            existing_user.selected_plan = user_data.selected_plan
            t = _tokens(existing_user)
            t.verification_token = code_hash
            t.verification_attempts = 0
            t.verification_expires_at = expires
            db.add(existing_user)
            user_to_send = existing_user
    else:
        user_id = str(uuid.uuid4())
        new_user = models.User(
            id=user_id,
            name=user_data.name,
            email=user_data.email,
            password_hash=hashed,
            role=safe_role,
            phone=user_data.phone,
            is_approved=False,
            created_at=datetime.utcnow(),
            is_email_verified=False,
            profile=models.UserProfile(company_logo=user_data.company_logo),
            subscription=models.UserSubscription(payment_status="unpaid", selected_plan=user_data.selected_plan),
            auth_tokens=models.UserAuthTokens(
                verification_token=code_hash,
                verification_expires_at=expires,
                verification_attempts=0,
            ),
        )
        db.add(new_user)
        user_to_send = new_user

    await db.commit()
    await db.refresh(user_to_send)
    
    background_tasks.add_task(
        send_email,
        user_to_send.email,
        "Verify Your Email Address — SpaceIO CRM",
        get_verification_html(code)
    )
    
    amount = 49900 if user_data.selected_plan == "monthly" else 499900
    
    return {
        "message": "Registration successful! A verification code has been sent to your email.",
        "user": user_to_send,
        "razorpay_order_id": None,
        "razorpay_key_id": RAZORPAY_KEY_ID,
        "amount": amount,
        "currency": "INR"
    }

@router.post("/login", response_model=schemas.LoginTokenResponse, dependencies=[Depends(rate_limit("login", 10, 300))])
async def login(credentials: schemas.UserLogin, db: AsyncSession = Depends(get_db)):
    U = models.User
    # Only what the login checks need: subscription is joined, profile (large base64 logo) and OTP/reset tokens are not loaded.
    # The client fetches the profile afterwards with GET /auth/me.
    result = await db.execute(
        select(U).options(load_only(
            U.id, U.name, U.email, U.password_hash, U.role, U.phone, U.is_email_verified,
            U.is_approved, U.token_version,
        ), lazyload(U.profile)).where(U.email == credentials.email)
    )
    user = result.scalars().first()
    # bcrypt is CPU-bound: run it off the event loop so other requests aren't blocked.
    if not user or not await run_in_threadpool(verify_password, credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
        
    if not user.is_email_verified:
        raise HTTPException(
            status_code=403,
            detail="email_unverified"
        )
        
    # Admin and Sales accounts are not subscription-based: skip all payment / access-period checks.
    needs_subscription = user.role not in ("Admin", "Sales")

    if needs_subscription and user.payment_status == "unpaid":
        amount = 49900 if user.selected_plan == "monthly" else 499900
        
        if not razor_client:
            raise HTTPException(
                status_code=500,
                detail="Razorpay payment gateway is not configured on the server. Cannot complete payment."
            )
        try:
            order = await run_in_threadpool(razor_client.order.create, data={"amount": amount, "currency": "INR", "receipt": user.id})
            razorpay_order_id = order.get("id")
        except Exception as e:
            logger.error(f"Razorpay Order creation on login error: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to generate payment order. Please try again."
            )
            
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_pending",
                "user_id": user.id,
                "email": user.email,
                "phone": user.phone,
                "name": user.name,
                "razorpay_order_id": razorpay_order_id,
                "razorpay_key_id": RAZORPAY_KEY_ID,
                "amount": amount,
                "plan": user.selected_plan
            }
        )

    if not user.is_approved:
        raise HTTPException(
            status_code=403, 
            detail="Your account registration is pending approval by an administrator. You will be able to log in once approved."
        )
        
    now = datetime.utcnow()
    if needs_subscription and user.access_start is not None and now < user.access_start:
        raise HTTPException(
            status_code=403,
            detail=f"Your account access period has not started yet (scheduled for {user.access_start})."
        )
    if needs_subscription and user.access_end is not None and now > user.access_end:
        amount = 49900 if (user.selected_plan or "monthly") == "monthly" else 499900
        razorpay_order_id = None
        if razor_client:
            try:
                order = await run_in_threadpool(razor_client.order.create, data={"amount": amount, "currency": "INR", "receipt": user.id})
                razorpay_order_id = order.get("id")
            except Exception as e:
                logger.error(f"Razorpay Order creation on expired login error: {e}")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "access_expired",
                "user_id": user.id,
                "email": user.email,
                "phone": user.phone,
                "name": user.name,
                "razorpay_order_id": razorpay_order_id,
                "razorpay_key_id": RAZORPAY_KEY_ID,
                "amount": amount,
                "plan": user.selected_plan or "monthly",
                "message": "Your account access has expired. Please select a plan and complete payment to renew access."
            }
        )

    if needs_subscription and user.status == "deactivated":
        raise HTTPException(status_code=403, detail="Your account has been deactivated. Please contact an administrator.")

    token = await _issue_session(user, db)
    return {"access_token": token, "token_type": "bearer"}

async def _issue_session(user: models.User, db: AsyncSession) -> str:
    """Start a new session: bump token_version so every other device/tab is signed out."""
    user.token_version = (user.token_version or 0) + 1
    db.add(user)
    await db.commit()
    set_cached_token_version(user.id, user.token_version)
    return create_access_token({"sub": user.id, "role": user.role}, token_version=user.token_version)

@router.post("/refresh", response_model=schemas.LoginTokenResponse, dependencies=[Depends(rate_limit("refresh", 60, 300))])
async def refresh_token(
    current_user_id: str = Depends(require_authenticated_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Sliding session: issue a fresh token while the user is active (same token_version, so other devices stay in sync)."""
    row = (await db.execute(
        select(models.User.role, models.User.token_version).where(models.User.id == current_user_id)
    )).first()
    if not row:
        raise HTTPException(status_code=401, detail="Session expired")
    role, token_version = row
    return {
        "access_token": create_access_token({"sub": current_user_id, "role": role}, token_version=token_version or 0),
        "token_type": "bearer",
    }

@router.post("/logout")
async def logout(
    current_user_id: str = Depends(require_authenticated_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Sign out everywhere: invalidates every token issued so far for this account."""
    result = await db.execute(select(models.User).where(models.User.id == current_user_id))
    user = result.scalars().first()
    if user:
        user.token_version = (user.token_version or 0) + 1
        db.add(user)
        await db.commit()
        set_cached_token_version(user.id, user.token_version)
    return {"message": "Signed out on all devices."}

@router.post("/create-subscription-order", dependencies=[Depends(rate_limit("sub-order", 10, 600))])
async def create_subscription_order(payload: schemas.CreateSubscriptionOrderRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.id == payload.user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
        
    amount = 49900 if payload.selected_plan == "monthly" else 499900
    
    if not razor_client:
        raise HTTPException(
            status_code=500,
            detail="Razorpay payment gateway is not configured on the server."
        )
    try:
        order = razor_client.order.create(data={"amount": amount, "currency": "INR", "receipt": user.id})
        razorpay_order_id = order.get("id")
    except Exception as e:
        logger.error(f"Razorpay Order creation error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate payment order. Please try again."
        )
        
    user.selected_plan = payload.selected_plan
    db.add(user)
    await db.commit()
    
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "phone": user.phone,
        "razorpay_order_id": razorpay_order_id,
        "razorpay_key_id": RAZORPAY_KEY_ID,
        "amount": amount,
        "plan": payload.selected_plan
    }

@router.post("/verify-email", dependencies=[Depends(rate_limit("verify-email", 10, 600))])
async def verify_email(payload: schemas.EmailVerifyRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.User).options(_WITH_TOKENS, lazyload(models.User.profile)).where(models.User.email == payload.email)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification code.")
        
    if not user.is_email_verified:
        t = _tokens(user)
        if (t.verification_attempts or 0) >= OTP_MAX_ATTEMPTS:
            t.verification_token = None
            db.add(user)
            await db.commit()
            raise HTTPException(status_code=429, detail="Too many incorrect attempts. Please register again to get a new code.")

        if not otp_matches(t.verification_token, payload.code):
            t.verification_attempts = (t.verification_attempts or 0) + 1
            db.add(user)
            await db.commit()
            raise HTTPException(status_code=400, detail="Invalid verification code.")
            
        if not t.verification_expires_at or t.verification_expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Verification code has expired. Please register again.")
            
        user.is_email_verified = True
        t.verification_token = None
        t.verification_expires_at = None
        t.verification_attempts = 0
        db.add(user)
        await db.commit()
        
    amount = 49900 if user.selected_plan == "monthly" else 499900
    
    if not razor_client:
        raise HTTPException(
            status_code=500,
            detail="Razorpay payment gateway is not configured on the server. Please contact an administrator."
        )
    try:
        order_data = {
            "amount": amount,
            "currency": "INR",
            "receipt": user.id
        }
        order = razor_client.order.create(data=order_data)
        razorpay_order_id = order.get("id")
    except Exception as e:
        logger.error(f"Razorpay Order creation error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to create payment order with Razorpay. Please try again."
        )
        
    return {
        "message": "Email verified successfully! Please complete your subscription payment.",
        "user_id": user.id,
        "razorpay_order_id": razorpay_order_id,
        "razorpay_key_id": RAZORPAY_KEY_ID,
        "amount": amount,
        "currency": "INR"
    }

@router.post("/verify-payment", response_model=schemas.TokenResponse, dependencies=[Depends(rate_limit("verify-payment", 10, 600))])
async def verify_payment(payload: schemas.PaymentVerifyRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    signature_valid = False
    
    if razor_client:
        try:
            params_dict = {
                'razorpay_order_id': payload.razorpay_order_id,
                'razorpay_payment_id': payload.razorpay_payment_id,
                'razorpay_signature': payload.razorpay_signature
            }
            razor_client.utility.verify_payment_signature(params_dict)
            signature_valid = True
        except Exception as e:
            logger.error(f"Razorpay payment verification failed: {e}")
            
    if not signature_valid:
        raise HTTPException(status_code=400, detail="Invalid payment signature. Payment verification failed.")
        
    # Replay protection: each Razorpay payment / order may be redeemed only once
    dup = await db.execute(
        select(models.SubscriptionPayment.id).where(
            (models.SubscriptionPayment.razorpay_payment_id == payload.razorpay_payment_id)
            | (models.SubscriptionPayment.razorpay_order_id == payload.razorpay_order_id)
        )
    )
    if dup.first():
        raise HTTPException(status_code=400, detail="This payment has already been processed.")

    # Trust Razorpay's record of the order (not the client) for amount / plan / owner
    try:
        order = razor_client.order.fetch(payload.razorpay_order_id)
    except Exception as e:
        logger.error(f"Razorpay order fetch failed: {e}")
        raise HTTPException(status_code=400, detail="Could not verify the payment order.")
    if order.get("receipt") != payload.user_id:
        raise HTTPException(status_code=400, detail="Payment does not belong to this account.")
    plan_by_amount = {49900: "monthly", 499900: "yearly"}
    verified_plan = plan_by_amount.get(order.get("amount"))
    if not verified_plan:
        raise HTTPException(status_code=400, detail="Unrecognised payment amount.")
    payload.selected_plan = verified_plan

    now = datetime.utcnow()
    
    user = None
    if payload.user_id:
        result = await db.execute(select(models.User).where(models.User.id == payload.user_id))
        user = result.scalars().first()
    if not user and payload.email:
        result = await db.execute(select(models.User).where(models.User.email == payload.email))
        user = result.scalars().first()
        
    if not user:
        user_id = payload.user_id or str(uuid.uuid4())
        hashed = hash_password(payload.password) if payload.password else ""
        access_end = now + timedelta(days=30) if payload.selected_plan == "monthly" else now + timedelta(days=365)
        user = models.User(
            id=user_id,
            name=payload.name or "User",
            email=payload.email,
            password_hash=hashed,
            role="Designer",
            phone=payload.phone,
            is_approved=True,
            is_email_verified=True,
            profile=models.UserProfile(company_logo=payload.company_logo),
            subscription=models.UserSubscription(
                payment_status="paid",
                selected_plan=payload.selected_plan or "monthly",
                access_start=now,
                access_end=access_end,
                status="active",
            ),
        )
        db.add(user)
    else:
        user.payment_status = "paid"
        user.status = "active"
        user.is_approved = True
        user.is_email_verified = True
        
        if payload.name:
            user.name = payload.name
        if payload.phone:
            user.phone = payload.phone
        if payload.company_logo:
            user.company_logo = payload.company_logo
        if payload.selected_plan:
            user.selected_plan = payload.selected_plan
            
        start_date = user.access_end if (user.access_end and user.access_end > now) else now
        user.access_start = now
        
        if user.selected_plan == "monthly":
            user.access_end = start_date + timedelta(days=30)
        else:
            user.access_end = start_date + timedelta(days=365)
            
        db.add(user)
        
    sub_payment = models.SubscriptionPayment(
        id=str(uuid.uuid4()),
        user_id=user.id,
        razorpay_order_id=payload.razorpay_order_id,
        razorpay_payment_id=payload.razorpay_payment_id,
        razorpay_signature=payload.razorpay_signature,
        plan=user.selected_plan,
        amount=499.0 if user.selected_plan == "monthly" else 4999.0,
        access_start=user.access_start,
        access_end=user.access_end
    )
    db.add(sub_payment)
    await db.commit()
    await db.refresh(user)
    
    try:
        access_end_str = user.access_end.strftime("%d %B %Y") if user.access_end else "N/A"
        paid_amount = 499.0 if user.selected_plan == "monthly" else 4999.0
        email_html = get_payment_confirmation_html(
            user_name=user.name,
            plan=user.selected_plan,
            amount=paid_amount,
            payment_id=payload.razorpay_payment_id,
            order_id=payload.razorpay_order_id,
            access_end_str=access_end_str
        )
        background_tasks.add_task(
            send_email,
            user.email,
            f"Payment Confirmation & Subscription Active - SpaceIO CRM ({user.selected_plan.capitalize()} Plan)",
            email_html
        )
    except Exception as email_err:
        logger.error(f"Error scheduling payment confirmation email: {email_err}")
    
    token = await _issue_session(user, db)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user
    }

@router.post("/redeem-coupon", response_model=schemas.TokenResponse, dependencies=[Depends(rate_limit("redeem-coupon", 10, 600))])
async def redeem_coupon(payload: schemas.CouponRedeemRequest, db: AsyncSession = Depends(get_db)):
    """Grant free access (all features) for the coupon's free_days. Used from the payment-pending /
    access-expired screen, where the caller holds the user_id returned by a correct-password login."""
    invalid = HTTPException(status_code=400, detail="Invalid or expired coupon code.")
    code = payload.code.strip().upper()

    user = (await db.execute(select(models.User).where(models.User.id == payload.user_id))).scalars().first()
    if not user or user.role in ("Admin", "Sales"):
        raise HTTPException(status_code=404, detail="User not found.")

    now = datetime.utcnow()
    # Row lock serialises concurrent redemptions so max_redemptions / once-per-user cannot be raced.
    coupon = (await db.execute(
        select(models.Coupon).where(models.Coupon.code == code).with_for_update()
    )).scalars().first()
    if (not coupon or not coupon.is_active
            or (coupon.valid_until and now > coupon.valid_until)
            or (coupon.max_redemptions is not None and coupon.redeemed_count >= coupon.max_redemptions)):
        raise invalid

    already = await db.execute(
        select(models.CouponRedemption.id).where(
            models.CouponRedemption.code == code, models.CouponRedemption.user_id == user.id
        )
    )
    if already.first():
        raise HTTPException(status_code=400, detail="You have already used this coupon.")

    start_date = user.access_end if (user.access_end and user.access_end > now) else now
    user.payment_status = "paid"
    user.status = "active"
    user.is_approved = True
    user.access_start = now
    user.access_end = start_date + timedelta(days=coupon.free_days)

    coupon.redeemed_count = (coupon.redeemed_count or 0) + 1
    redemption_id = str(uuid.uuid4())
    db.add(models.CouponRedemption(id=redemption_id, code=code, user_id=user.id))
    # Shows up in the user's payment history as a zero-amount entry
    db.add(models.SubscriptionPayment(
        id=str(uuid.uuid4()),
        user_id=user.id,
        razorpay_order_id=None,
        razorpay_payment_id=f"COUPON-{code}-{redemption_id[:8]}",
        razorpay_signature=None,
        plan=user.selected_plan,
        amount=0.0,
        access_start=user.access_start,
        access_end=user.access_end,
    ))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    clear_cached_token_version(user.id)  # drop any cached "blocked" state

    token = await _issue_session(user, db)
    return {"access_token": token, "token_type": "bearer", "user": user}

@router.post("/forgot-password", dependencies=[Depends(rate_limit("forgot-password", 5, 600))])
async def forgot_password(payload: schemas.PasswordResetRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.User).options(_WITH_TOKENS, lazyload(models.User.profile)).where(models.User.email == payload.email)
    )
    user = result.scalars().first()
    
    if user:
        code = generate_otp()
        t = _tokens(user)
        t.reset_token = hash_otp(code)
        t.reset_attempts = 0
        t.reset_expires_at = datetime.utcnow() + timedelta(minutes=15)
        db.add(user)
        await db.commit()
        
        background_tasks.add_task(
            send_email,
            user.email,
            "Password Reset Code — SpaceIO CRM",
            get_reset_password_html(code)
        )
        
        return {"message": "If the email is registered, a password reset code has been sent."}
        
    return {"message": "If the email is registered, a password reset code has been sent."}

@router.post("/reset-password", dependencies=[Depends(rate_limit("reset-password", 10, 600))])
async def reset_password(payload: schemas.PasswordResetConfirm, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.User).options(_WITH_TOKENS, lazyload(models.User.profile)).where(models.User.email == payload.email)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or incorrect reset code.")

    t = _tokens(user)
    if (t.reset_attempts or 0) >= OTP_MAX_ATTEMPTS:
        t.reset_token = None
        db.add(user)
        await db.commit()
        raise HTTPException(status_code=429, detail="Too many incorrect attempts. Please request a new code.")

    if not otp_matches(t.reset_token, payload.token):
        t.reset_attempts = (t.reset_attempts or 0) + 1
        db.add(user)
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid or incorrect reset code.")
        
    if not t.reset_expires_at or t.reset_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Reset code has expired. Please request a new one.")
        
    user.password_hash = hash_password(payload.new_password)
    t.reset_token = None
    t.reset_expires_at = None
    t.reset_attempts = 0
    user.token_version = (user.token_version or 0) + 1  # sign out all sessions after a password reset
    db.add(user)
    await db.commit()
    clear_cached_token_version(user.id)
    
    return {"message": "Password reset successful! You can now log in with your new password."}
