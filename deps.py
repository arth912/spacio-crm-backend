import os
import time
import secrets
import hmac
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional
import jwt
import bcrypt
import razorpay
from fastapi import Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import load_only

from db import get_db
import models

logger = logging.getLogger(__name__)

# JWT Constants
from dotenv import load_dotenv
load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY must be set in the environment (at least 32 characters). "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))  # sliding, renewed via /auth/refresh

# Razorpay Constants
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

try:
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
        razor_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    else:
        razor_client = None
except Exception as e:
    logger.error(f"Error initializing Razorpay Client: {e}")
    razor_client = None


# --- PASSWORD UTILS ---
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

# --- JWT UTILS ---
# token_version cache: user_id -> (version, cached_at). Keeps auth off the DB for most requests.
# Per-process cache: with several workers a revoked token may stay valid for up to _VER_TTL seconds.
_VER_TTL = 15
_ver_cache: dict = {}

def create_access_token(data: dict, token_version: int = 0) -> str:
    to_encode = data.copy()
    now = datetime.utcnow()
    to_encode.update({
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": now,
        "jti": secrets.token_hex(8),
        "ver": token_version,
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def set_cached_token_version(user_id: str, version: int, blocked: bool = False) -> None:
    _ver_cache[user_id] = (version, time.monotonic(), blocked)

def clear_cached_token_version(user_id: str) -> None:
    _ver_cache.pop(user_id, None)

async def _current_token_version(user_id: str, db: AsyncSession) -> Optional[tuple]:
    """Returns (token_version, blocked) or None if the user no longer exists.
    blocked = a subscription-based user (not Admin/Sales) whose account is deactivated or whose
    access period has ended. Cached together with the version, so no extra query per request."""
    cached = _ver_cache.get(user_id)
    if cached and time.monotonic() - cached[1] < _VER_TTL:
        return cached[0], cached[2]
    U, S = models.User, models.UserSubscription
    result = await db.execute(
        select(U.token_version, U.role, S.status, S.access_end)
        .outerjoin(S, S.user_id == U.id)
        .where(U.id == user_id)
    )
    row = result.first()
    if row is None:
        _ver_cache.pop(user_id, None)
        return None
    version = row[0] or 0
    needs_subscription = row[1] not in ("Admin", "Sales")
    expired = row[3] is not None and datetime.utcnow() > row[3]
    blocked = needs_subscription and (row[2] == "deactivated" or expired)
    set_cached_token_version(user_id, version, blocked)
    return version, blocked

_credentials_error = lambda detail="Could not validate credentials": HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail=detail,
    headers={"WWW-Authenticate": "Bearer"},
)

async def _authenticate(token: str, db: AsyncSession) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise _credentials_error("Invalid or expired authentication token")
    user_id = payload.get("sub")
    if not user_id:
        raise _credentials_error("Invalid authentication payload")
    current = await _current_token_version(user_id, db)
    if current is None or payload.get("ver", 0) != current[0]:
        raise _credentials_error("Session expired or signed in on another device")
    if current[1]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_deactivated")
    return payload

def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise _credentials_error("Authentication token is required to access data")
    return authorization.split(" ", 1)[1].strip()

async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> models.User:
    payload = await _authenticate(_bearer_token(authorization), db)
    U = models.User
    # Only the columns UserResponse serializes: skips password_hash. profile + subscription are joined eagerly.
    result = await db.execute(
        select(U).options(load_only(
            U.id, U.name, U.email, U.role, U.phone, U.is_approved, U.created_at,
        )).where(U.id == payload["sub"])
    )
    user = result.scalars().first()
    if user is None:
        raise _credentials_error()
    return user

async def require_authenticated_user_id(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> str:
    payload = await _authenticate(_bearer_token(authorization), db)
    return payload["sub"]

# Backwards-compatible alias: identity now ONLY comes from a verified Bearer token.
get_user_id_from_request = require_authenticated_user_id


# --- OTP UTILS ---
OTP_MAX_ATTEMPTS = 5

def generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000}"

def hash_otp(code: str) -> str:
    return hmac.new(SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()

def otp_matches(stored_hash: Optional[str], code: str) -> bool:
    return bool(stored_hash) and hmac.compare_digest(stored_hash, hash_otp(code))


async def require_admin(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Dependency: caller must be an approved Admin. Returns the user id."""
    payload = await _authenticate(_bearer_token(authorization), db)
    uid = payload["sub"]
    row = (await db.execute(
        select(models.User.role, models.User.is_approved).where(models.User.id == uid)
    )).first()
    if not row or row[0] != "Admin" or not row[1]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return uid
