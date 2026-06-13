import os
import sys

# Patch cffi dynamic library loader to search Homebrew paths on macOS before weasyprint is imported
if sys.platform == 'darwin':
    try:
        import cffi
        orig_dlopen = cffi.FFI.dlopen
        def custom_dlopen(self, name, flags=0):
            if name and not name.startswith('/') and not os.path.exists(name):
                for prefix in ['/opt/homebrew/lib', '/usr/local/lib']:
                    clean_name = name
                    if name.startswith('lib'):
                        clean_name = name[3:]
                    # Remove trailing version suffixes
                    for suffix in ['-2.0-0', '-1.0-0', '-2.0', '-1.0', '-0']:
                        if clean_name.endswith(suffix):
                            clean_name = clean_name[:-len(suffix)]
                    
                    possible_names = [
                        os.path.join(prefix, name),
                        os.path.join(prefix, f'lib{clean_name}.dylib'),
                        os.path.join(prefix, f'lib{clean_name}-2.0.dylib'),
                        os.path.join(prefix, f'lib{clean_name}-1.0.dylib'),
                        os.path.join(prefix, f'lib{clean_name}.0.dylib'),
                        os.path.join(prefix, f'lib{clean_name}.1.dylib'),
                    ]
                    for p_name in possible_names:
                        if os.path.exists(p_name):
                            try:
                                return orig_dlopen(self, p_name, flags)
                            except OSError:
                                pass
            return orig_dlopen(self, name, flags)
        cffi.FFI.dlopen = custom_dlopen
    except Exception as e:
        print(f"Failed to patch cffi dlopen: {e}")

import uuid
import random
from datetime import datetime, timedelta
from typing import List, Optional
import jwt
import bcrypt
from fastapi import FastAPI, Depends, HTTPException, status, Response, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, desc, func

# Import local structures
from db import get_db, engine, Base
import models
import schemas
from pricing import calculate_item_price
from pdf_generator import generate_quotation_pdf
import razorpay
import hmac
import hashlib

# Import email utils
from email_utils import send_email, get_verification_html, get_reset_password_html

# Razorpay Constants
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

try:
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
        razor_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    else:
        razor_client = None
except Exception as e:
    print(f"Error initializing Razorpay Client: {e}")
    razor_client = None


# JWT Constants
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "decocrm_secure_jwt_token_development_key_2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440 # 24 Hours

app = FastAPI(
    title="decoCRM API Engine",
    description="Backend API for decoCRM - Interior Design Quotation & Billing Management",
    version="1.0.0"
)

# CORS Setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- PASSWORD UTILS ---
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

# --- JWT UTILS ---
def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str, db: AsyncSession = Depends(get_db)) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
        
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    return user

async def get_user_id_from_request(
    authorization: Optional[str] = Header(None),
    user_id: Optional[str] = None
) -> Optional[str]:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload.get("sub")
        except Exception:
            pass
    return user_id

# --- AUTH ENDPOINTS ---
@app.post("/api/v1/auth/register", response_model=schemas.RegisterResponse)
async def register(user_data: schemas.UserRegister, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    # Check if email is already taken
    result = await db.execute(select(models.User).where(models.User.email == user_data.email))
    existing_user = result.scalars().first()
    
    hashed = hash_password(user_data.password)
    code = f"{random.randint(100000, 999999)}"
    expires = datetime.utcnow() + timedelta(minutes=15)
    
    if existing_user:
        if existing_user.is_email_verified:
            raise HTTPException(status_code=400, detail="Email is already registered")
        else:
            # Overwrite unverified user details to retry signup
            existing_user.name = user_data.name
            existing_user.password_hash = hashed
            existing_user.phone = user_data.phone
            existing_user.company_logo = user_data.company_logo
            existing_user.selected_plan = user_data.selected_plan
            existing_user.email_verification_token = code
            existing_user.email_verification_expires_at = expires
            db.add(existing_user)
            user_to_send = existing_user
    else:
        user_id = str(uuid.uuid4())
        new_user = models.User(
            id=user_id,
            name=user_data.name,
            email=user_data.email,
            password_hash=hashed,
            role=user_data.role,
            phone=user_data.phone,
            company_logo=user_data.company_logo,
            is_approved=False,
            created_at=datetime.utcnow(),
            payment_status="unpaid",
            selected_plan=user_data.selected_plan,
            is_email_verified=False,
            email_verification_token=code,
            email_verification_expires_at=expires
        )
        db.add(new_user)
        user_to_send = new_user

    await db.commit()
    await db.refresh(user_to_send)
    
    # Send verification email in background
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


@app.post("/api/v1/auth/login", response_model=schemas.TokenResponse)
async def login(credentials: schemas.UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.email == credentials.email))
    user = result.scalars().first()
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
        
    if not user.is_email_verified:
        raise HTTPException(
            status_code=403,
            detail="email_unverified"
        )
        
    if user.payment_status == "unpaid" and user.role != "Admin":
        amount = 49900 if user.selected_plan == "monthly" else 499900
        
        if not razor_client:
            raise HTTPException(
                status_code=500,
                detail="Razorpay payment gateway is not configured on the server. Cannot complete payment."
            )
        try:
            order = razor_client.order.create(data={"amount": amount, "currency": "INR", "receipt": user.id})
            razorpay_order_id = order.get("id")
        except Exception as e:
            print(f"Razorpay Order creation on login error: {e}")
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
    if user.access_start is not None and now < user.access_start:
        raise HTTPException(
            status_code=403,
            detail=f"Your account access period has not started yet (scheduled for {user.access_start})."
        )
    if user.access_end is not None and now > user.access_end:
        raise HTTPException(
            status_code=403,
            detail="Your account access has expired. Please contact an administrator."
        )
        
    token = create_access_token({"sub": user.id, "role": user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user
    }

@app.post("/api/v1/auth/verify-email")
async def verify_email(payload: schemas.EmailVerifyRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.email == payload.email))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User registration record not found.")
        
    if not user.is_email_verified:
        if not user.email_verification_token or user.email_verification_token != payload.code:
            raise HTTPException(status_code=400, detail="Invalid verification code.")
            
        if not user.email_verification_expires_at or user.email_verification_expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Verification code has expired. Please register again.")
            
        user.is_email_verified = True
        user.email_verification_token = None
        user.email_verification_expires_at = None
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
    # Generate Razorpay Order details for the frontend
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
        print(f"Razorpay Order creation error: {e}")
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

@app.post("/api/v1/auth/verify-payment", response_model=schemas.TokenResponse)
async def verify_payment(payload: schemas.PaymentVerifyRequest, db: AsyncSession = Depends(get_db)):
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
            print(f"Razorpay payment verification failed: {e}")
            
    if not signature_valid:
        raise HTTPException(status_code=400, detail="Invalid payment signature. Payment verification failed.")
        
    now = datetime.utcnow()
    
    # Check if user already exists
    user = None
    if payload.user_id:
        result = await db.execute(select(models.User).where(models.User.id == payload.user_id))
        user = result.scalars().first()
    if not user and payload.email:
        result = await db.execute(select(models.User).where(models.User.email == payload.email))
        user = result.scalars().first()
        
    if not user:
        # Fallback creation
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
            company_logo=payload.company_logo,
            is_approved=True,
            payment_status="paid",
            selected_plan=payload.selected_plan or "monthly",
            access_start=now,
            access_end=access_end,
            is_email_verified=True
        )
        db.add(user)
    else:
        # Update existing verified user details
        user.payment_status = "paid"
        user.is_approved = True
        user.is_email_verified = True # Ensure email verified
        user.access_start = now
        
        if payload.name:
            user.name = payload.name
        if payload.phone:
            user.phone = payload.phone
        if payload.company_logo:
            user.company_logo = payload.company_logo
        if payload.selected_plan:
            user.selected_plan = payload.selected_plan
            
        if user.selected_plan == "monthly":
            user.access_end = now + timedelta(days=30)
        else:
            user.access_end = now + timedelta(days=365)
            
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
    
    token = create_access_token({"sub": user.id, "role": user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user
    }

@app.post("/api/v1/auth/forgot-password")
async def forgot_password(payload: schemas.PasswordResetRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.email == payload.email))
    user = result.scalars().first()
    
    if user:
        code = f"{random.randint(100000, 999999)}"
        user.reset_token = code
        user.reset_token_expires_at = datetime.utcnow() + timedelta(minutes=15)
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

@app.post("/api/v1/auth/reset-password")
async def reset_password(payload: schemas.PasswordResetConfirm, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.email == payload.email))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
        
    if not user.reset_token or user.reset_token != payload.token:
        raise HTTPException(status_code=400, detail="Invalid or incorrect reset code.")
        
    if not user.reset_token_expires_at or user.reset_token_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Reset code has expired. Please request a new one.")
        
    hashed = hash_password(payload.new_password)
    user.password_hash = hashed
    user.reset_token = None
    user.reset_token_expires_at = None
    db.add(user)
    await db.commit()
    
    return {"message": "Password has been reset successfully. You can now sign in."}



@app.get("/api/v1/auth/me", response_model=schemas.UserResponse)
async def get_me(token: str, db: AsyncSession = Depends(get_db)):
    return await get_current_user(token, db)

@app.put("/api/v1/auth/profile", response_model=schemas.UserResponse)
async def update_profile(
    profile_data: schemas.UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
        
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
        
    if profile_data.new_password:
        user.password_hash = hash_password(profile_data.new_password)
        
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# --- ADMIN USER MANAGEMENT ENDPOINTS ---
@app.get("/api/v1/users", response_model=List[schemas.UserResponse])
async def get_all_users(
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    admin_res = await db.execute(select(models.User).where(models.User.id == current_user_id))
    admin = admin_res.scalars().first()
    if not admin or admin.role != "Admin" or not admin.is_approved:
        raise HTTPException(status_code=403, detail="Admin access required")
        
    result = await db.execute(select(models.User).order_by(desc(models.User.created_at)))
    return result.scalars().all()

@app.put("/api/v1/users/{user_id}/approve", response_model=schemas.UserResponse)
async def approve_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    admin_res = await db.execute(select(models.User).where(models.User.id == current_user_id))
    admin = admin_res.scalars().first()
    if not admin or admin.role != "Admin" or not admin.is_approved:
        raise HTTPException(status_code=403, detail="Admin access required")
        
    user_res = await db.execute(select(models.User).where(models.User.id == user_id))
    user = user_res.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    user.is_approved = True
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@app.put("/api/v1/users/{user_id}/role", response_model=schemas.UserResponse)
async def update_user_role(
    user_id: str,
    role_data: schemas.UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    admin_res = await db.execute(select(models.User).where(models.User.id == current_user_id))
    admin = admin_res.scalars().first()
    if not admin or admin.role != "Admin" or not admin.is_approved:
        raise HTTPException(status_code=403, detail="Admin access required")
        
    user_res = await db.execute(select(models.User).where(models.User.id == user_id))
    user = user_res.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # If self-editing, prevent role or date changes
    if user.id == current_user_id:
        if role_data.role != user.role:
            raise HTTPException(status_code=400, detail="Cannot change your own role")
        if role_data.access_start is not None or role_data.access_end is not None:
            raise HTTPException(status_code=400, detail="Cannot change your own access dates")

    # Validate email uniqueness if it is changing
    if role_data.email and role_data.email != user.email:
        email_check = await db.execute(select(models.User).where(models.User.email == role_data.email))
        if email_check.scalars().first():
            raise HTTPException(status_code=400, detail="Email is already registered by another account")
        user.email = role_data.email

    # Update profile fields
    if role_data.name is not None:
        user.name = role_data.name
    if role_data.phone is not None:
        user.phone = role_data.phone
    if role_data.company_logo is not None:
        user.company_logo = role_data.company_logo
    if role_data.general_terms is not None:
        user.general_terms = role_data.general_terms

    # Update role and dates if not self-editing
    if user.id != current_user_id:
        user.role = role_data.role
        user.access_start = role_data.access_start.replace(tzinfo=None) if role_data.access_start is not None else None
        user.access_end = role_data.access_end.replace(tzinfo=None) if role_data.access_end is not None else None

    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@app.delete("/api/v1/users/{user_id}")
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    admin_res = await db.execute(select(models.User).where(models.User.id == current_user_id))
    admin = admin_res.scalars().first()
    if not admin or admin.role != "Admin" or not admin.is_approved:
        raise HTTPException(status_code=403, detail="Admin access required")
        
    user_res = await db.execute(select(models.User).where(models.User.id == user_id))
    user = user_res.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user.id == current_user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account")
        
    await db.delete(user)
    await db.commit()
    return {"message": "User deleted successfully"}


@app.get("/api/v1/users/{user_id}/subscriptions", response_model=List[schemas.SubscriptionPaymentResponse])
async def get_user_subscriptions(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    admin_res = await db.execute(select(models.User).where(models.User.id == current_user_id))
    admin = admin_res.scalars().first()
    if not admin or admin.role != "Admin" or not admin.is_approved:
        raise HTTPException(status_code=403, detail="Admin access required")
        
    result = await db.execute(
        select(models.SubscriptionPayment)
        .where(models.SubscriptionPayment.user_id == user_id)
        .order_by(desc(models.SubscriptionPayment.created_at))
    )
    return result.scalars().all()


# --- CLIENT ENDPOINTS ---
@app.post("/api/v1/clients", response_model=schemas.ClientResponse)
async def create_client(
    client_data: schemas.ClientCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
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

@app.get("/api/v1/clients", response_model=List[schemas.ClientResponse])
async def get_clients(
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    query = select(models.Client)
    if current_user_id:
        query = query.where(models.Client.user_id == current_user_id)
    result = await db.execute(query.order_by(models.Client.created_at.desc()))
    return result.scalars().all()

@app.get("/api/v1/clients/{client_id}", response_model=schemas.ClientResponse)
async def get_client(
    client_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    query = select(models.Client).where(models.Client.id == client_id)
    if current_user_id:
        query = query.where(models.Client.user_id == current_user_id)
    result = await db.execute(query)
    client = result.scalars().first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@app.put("/api/v1/clients/{client_id}", response_model=schemas.ClientResponse)
async def update_client(
    client_id: str,
    client_data: schemas.ClientUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    query = select(models.Client).where(models.Client.id == client_id)
    if current_user_id:
        query = query.where(models.Client.user_id == current_user_id)
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


# --- PROJECT ENDPOINTS ---
@app.post("/api/v1/projects", response_model=schemas.ProjectResponse)
async def create_project(
    project_data: schemas.ProjectCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    # Verify client exists and belongs to the user if specified
    c_query = select(models.Client).where(models.Client.id == project_data.client_id)
    if current_user_id:
        c_query = c_query.where(models.Client.user_id == current_user_id)
    c_res = await db.execute(c_query)
    if not c_res.scalars().first():
        raise HTTPException(status_code=404, detail="Client not found")
        
    new_project = models.Project(
        id=str(uuid.uuid4()),
        client_id=project_data.client_id,
        user_id=current_user_id,
        name=project_data.name,
        site_address=project_data.site_address,
        budget=project_data.budget,
        status=project_data.status
    )
    db.add(new_project)
    await db.flush()
    return new_project

@app.get("/api/v1/projects", response_model=List[schemas.ProjectResponse])
async def get_projects(
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    query = select(models.Project)
    if current_user_id:
        query = query.where(models.Project.user_id == current_user_id)
    result = await db.execute(query.order_by(models.Project.created_at.desc()))
    return result.scalars().all()

@app.get("/api/v1/projects/{project_id}", response_model=schemas.ProjectResponse)
async def get_project(
    project_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    query = select(models.Project).where(models.Project.id == project_id)
    if current_user_id:
        query = query.where(models.Project.user_id == current_user_id)
    result = await db.execute(query)
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@app.put("/api/v1/projects/{project_id}", response_model=schemas.ProjectResponse)
async def update_project(
    project_id: str, 
    project_data: schemas.ProjectUpdate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    query = select(models.Project).where(models.Project.id == project_id)
    if current_user_id:
        query = query.where(models.Project.user_id == current_user_id)
    result = await db.execute(query)
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    project.name = project_data.name
    if project_data.client_id is not None:
        # Verify client belongs to user
        c_query = select(models.Client).where(models.Client.id == project_data.client_id)
        if current_user_id:
            c_query = c_query.where(models.Client.user_id == current_user_id)
        c_res = await db.execute(c_query)
        if not c_res.scalars().first():
            raise HTTPException(status_code=400, detail="Client not found")
        project.client_id = project_data.client_id
    project.site_address = project_data.site_address
    project.budget = project_data.budget
    project.status = project_data.status
    
    await db.commit()
    await db.refresh(project)
    return project


# --- ROOM ENDPOINTS ---
@app.post("/api/v1/rooms", response_model=schemas.RoomResponse)
async def create_room(
    room_data: schemas.RoomCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    p_query = select(models.Project).where(models.Project.id == room_data.project_id)
    if current_user_id:
        p_query = p_query.where(models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Project not found")
        
    new_room = models.Room(
        id=str(uuid.uuid4()),
        project_id=room_data.project_id,
        room_name=room_data.room_name
    )
    db.add(new_room)
    await db.flush()
    return new_room

@app.get("/api/v1/projects/{project_id}/rooms", response_model=List[schemas.RoomResponse])
async def get_project_rooms(
    project_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    p_query = select(models.Project).where(models.Project.id == project_id)
    if current_user_id:
        p_query = p_query.where(models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(select(models.Room).where(models.Room.project_id == project_id))
    return result.scalars().all()


# --- MASTER CATALOG ENDPOINTS ---
@app.get("/api/v1/catalog/categories", response_model=List[schemas.CategoryResponse])
async def get_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Category).order_by(models.Category.name))
    return result.scalars().all()

@app.get("/api/v1/catalog/items", response_model=List[schemas.ItemResponse])
async def get_items(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Item).order_by(models.Item.subcategory, models.Item.name))
    return result.scalars().all()

@app.post("/api/v1/catalog/items", response_model=schemas.ItemResponse)
async def create_item(item_data: schemas.ItemCreate, db: AsyncSession = Depends(get_db)):
    category_res = await db.execute(select(models.Category).where(models.Category.id == item_data.category_id))
    if not category_res.scalars().first():
        raise HTTPException(status_code=404, detail="Category not found")

    existing_res = await db.execute(
        select(models.Item).where(
            models.Item.category_id == item_data.category_id,
            func.lower(models.Item.name) == item_data.name.strip().lower()
        )
    )
    existing_item = existing_res.scalars().first()
    if existing_item:
        return existing_item

    new_item = models.Item(
        id=str(uuid.uuid4()),
        category_id=item_data.category_id,
        subcategory=item_data.subcategory,
        name=item_data.name.strip(),
        pricing_type=item_data.pricing_type,
        brand=item_data.brand,
        base_rate=item_data.base_rate,
        labor_cost=item_data.labor_cost,
        material=item_data.material,
        dimensions=item_data.dimensions
    )
    db.add(new_item)
    await db.flush()
    return new_item

@app.get("/api/v1/catalog/categories/{category_id}/items", response_model=List[schemas.ItemResponse])
async def get_category_items(category_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Item).where(models.Item.category_id == category_id).order_by(models.Item.name))
    return result.scalars().all()


# --- QUOTATION BUILDER & VERSIONING ENGINE ---
@app.post("/api/v1/quotations", response_model=schemas.QuotationResponse)
async def create_quotation(
    q_data: schemas.QuotationCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    # 1. Verify project exists
    p_query = select(models.Project).where(models.Project.id == q_data.project_id)
    if current_user_id:
        p_query = p_query.where(models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    project = p_res.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    # 2. Determine revision version (Incremental logic)
    v_res = await db.execute(
        select(models.Quotation)
        .where(models.Quotation.project_id == q_data.project_id)
        .order_by(desc(models.Quotation.version))
    )
    latest_quote = v_res.scalars().first()
    version = 1
    
    if latest_quote:
        version = latest_quote.version + 1
        # Mark the previous version as revised
        latest_quote.status = "revised"
        db.add(latest_quote)
        
    # 3. Create core quotation entry
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
    await db.flush() # Populate the database ID
    
    subtotal_aggregate = 0.0
    gst_aggregate = 0.0
    
    # 4. Ingest quotation item references and run calculations
    for item_in in q_data.items:
        # Load item details from master catalog
        item_res = await db.execute(select(models.Item).where(models.Item.id == item_in.item_id))
        master_item = item_res.scalars().first()
        if not master_item:
            raise HTTPException(status_code=404, detail=f"Master item ID {item_in.item_id} not found in catalog")
            
        # Determine the pricing type (use the user-overridden one if provided, otherwise the master item's one)
        ptype = item_in.pricing_type if (item_in.pricing_type is not None and item_in.pricing_type != "") else master_item.pricing_type

        # Determine the base rate and labor cost (use user overrides if provided, else catalog defaults)
        brate = item_in.base_rate if item_in.base_rate is not None else master_item.base_rate
        lcost = item_in.labor_cost if item_in.labor_cost is not None else master_item.labor_cost

        # Run calculation
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
        
        # Save snapshot detail
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
        
        # Accumulate aggregates (subtotal is GST-inclusive total_amount of the items)
        subtotal_aggregate += pricing_breakdown["total_amount"]
        
    # 5. Apply additional global GST on final subtotal (sum of items including item-wise GST)
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
    
    # Reload quotation with loaded items for returning response
    return await get_quotation_details(quotation_id, db)


# Helper function to compile nested quotation response with all text lookups
async def get_quotation_details(quotation_id: str, db: AsyncSession) -> schemas.QuotationResponse:
    # Query quotation core details
    q_res = await db.execute(select(models.Quotation).where(models.Quotation.id == quotation_id))
    q = q_res.scalars().first()
    if not q:
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    # Look up project and client details
    p_res = await db.execute(select(models.Project).where(models.Project.id == q.project_id))
    project = p_res.scalars().first()
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
            
    # Load and map items
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

@app.get("/api/v1/quotations/{quotation_id}", response_model=schemas.QuotationResponse)
async def get_quotation(
    quotation_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    # Verify quotation belongs to user
    q_res = await db.execute(select(models.Quotation).where(models.Quotation.id == quotation_id))
    q = q_res.scalars().first()
    if not q:
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    p_query = select(models.Project).where(models.Project.id == q.project_id)
    if current_user_id:
        p_query = p_query.where(models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Quotation not found")

    return await get_quotation_details(quotation_id, db)

@app.get("/api/v1/projects/{project_id}/quotations", response_model=List[schemas.QuotationResponse])
async def get_project_quotations(
    project_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    p_query = select(models.Project).where(models.Project.id == project_id)
    if current_user_id:
        p_query = p_query.where(models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Project not found")

    q_res = await db.execute(
        select(models.Quotation)
        .where(models.Quotation.project_id == project_id)
        .order_by(desc(models.Quotation.version))
    )
    quotes = q_res.scalars().all()
    
    out = []
    for q in quotes:
        details = await get_quotation_details(q.id, db)
        out.append(details)
    return out

@app.put("/api/v1/quotations/{quotation_id}/status", response_model=schemas.QuotationResponse)
async def update_quotation_status(
    quotation_id: str, 
    status_data: schemas.QuotationStatusUpdate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    q_res = await db.execute(select(models.Quotation).where(models.Quotation.id == quotation_id))
    q = q_res.scalars().first()
    if not q:
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    p_query = select(models.Project).where(models.Project.id == q.project_id)
    if current_user_id:
        p_query = p_query.where(models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    q.status = status_data.status
    db.add(q)
    await db.commit()
    return await get_quotation_details(quotation_id, db)


# --- PDF DOWNLOAD ENDPOINT ---
@app.get("/api/v1/quotations/{quotation_id}/pdf")
async def download_quotation_pdf(
    quotation_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    # Verify ownership
    q_res = await db.execute(select(models.Quotation).where(models.Quotation.id == quotation_id))
    q = q_res.scalars().first()
    if not q:
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    p_query = select(models.Project).where(models.Project.id == q.project_id)
    if current_user_id:
        p_query = p_query.where(models.Project.user_id == current_user_id)
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
                "company_logo": user.company_logo,
                "general_terms": user.general_terms
            }

    # 1. Fetch detailed quotation data
    q_details = await get_quotation_details(quotation_id, db)
    q_dict = q_details.dict()
    q_dict["creator"] = creator_dict
    
    # Convert dates to string so they don't break jinja
    q_dict["created_at"] = q_dict["created_at"].strftime("%Y-%m-%d %H:%M")
    for item in q_dict.get("items", []):
        item["created_at"] = item["created_at"].strftime("%Y-%m-%d %H:%M")
        
    # 2. Compile to PDF bytes
    try:
        pdf_bytes = generate_quotation_pdf(q_dict)
        # 3. Stream back as PDF file attachment
        filename = f"Proposal_{q_details.client_name.replace(' ', '_')}_V{q_details.version}.pdf"
        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    except Exception as e:
        print(f"WeasyPrint failed: {e}. Falling back to print-friendly HTML template.")
        from fastapi.responses import HTMLResponse
        from pdf_generator import generate_quotation_html
        html_content = generate_quotation_html(q_dict, weasyprint_failed=True)
        return HTMLResponse(content=html_content, status_code=200)

# --- PAYMENT ENDPOINTS ---
@app.post("/api/v1/payments", response_model=schemas.PaymentResponse)
async def create_payment(
    payment_data: schemas.PaymentCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    proj_query = select(models.Project).where(models.Project.id == payment_data.project_id)
    if current_user_id:
        proj_query = proj_query.where(models.Project.user_id == current_user_id)
    proj_res = await db.execute(proj_query)
    proj = proj_res.scalars().first()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    new_payment = models.Payment(
        id=str(uuid.uuid4()),
        project_id=payment_data.project_id,
        amount=payment_data.amount,
        payment_date=payment_data.payment_date or datetime.utcnow(),
        payment_method=payment_data.payment_method,
        transaction_id=payment_data.transaction_id,
        notes=payment_data.notes
    )
    db.add(new_payment)
    await db.flush()
    return new_payment

@app.get("/api/v1/projects/{project_id}/payments", response_model=List[schemas.PaymentResponse])
async def get_project_payments(
    project_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    proj_query = select(models.Project).where(models.Project.id == project_id)
    if current_user_id:
        proj_query = proj_query.where(models.Project.user_id == current_user_id)
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

@app.get("/api/v1/payments", response_model=List[schemas.PaymentResponse])
async def get_all_payments(
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    query = select(models.Payment)
    if current_user_id:
        query = query.join(models.Project, models.Payment.project_id == models.Project.id).where(models.Project.user_id == current_user_id)
    pay_res = await db.execute(query.order_by(desc(models.Payment.payment_date)))
    return pay_res.scalars().all()

@app.delete("/api/v1/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_payment(
    payment_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: Optional[str] = Depends(get_user_id_from_request)
):
    query = select(models.Payment).where(models.Payment.id == payment_id)
    if current_user_id:
        query = query.join(models.Project, models.Payment.project_id == models.Project.id).where(models.Project.user_id == current_user_id)
    pay_res = await db.execute(query)
    payment = pay_res.scalars().first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found")
    
    await db.delete(payment)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

