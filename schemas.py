from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional
from datetime import date, datetime

LOGO_MAX = 1_500_000  # ~1.1 MB image as base64
BILLING_TEMPLATE_PATTERN = "^(classic|modern|minimal|professional)$"

# --- AUTH SCHEMAS ---
class UserRegister(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=72)
    role: str = Field("Designer", pattern="^(Admin|Designer|Sales)$")
    phone: str = Field(..., min_length=10, max_length=15)
    company_logo: Optional[str] = Field(None, max_length=LOGO_MAX)
    selected_plan: str = Field("monthly", pattern="^(monthly|yearly)$")

class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., max_length=72)

class UserResponse(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: str
    phone: Optional[str] = None
    company_logo: Optional[str] = None
    general_terms: Optional[str] = None
    billing_terms: Optional[str] = None
    signature: Optional[str] = None
    stamp: Optional[str] = None
    billing_template: str = "modern"
    tour_completed: bool = False
    is_approved: bool
    created_at: datetime
    access_start: Optional[datetime] = None
    access_end: Optional[datetime] = None
    payment_status: str
    selected_plan: str
    status: str = "active"


    class Config:
        from_attributes = True

class TourStatusUpdate(BaseModel):
    completed: bool = True

class UserListItem(BaseModel):
    """Admin list row: omits the large company_logo / general_terms (fetched per user on demand)."""
    id: str
    name: str
    email: EmailStr
    role: str
    phone: Optional[str] = None
    is_approved: bool
    created_at: datetime
    access_start: Optional[datetime] = None
    access_end: Optional[datetime] = None
    payment_status: str
    selected_plan: str
    status: str = "active"

class UserStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(active|deactivated)$")

class RegisterResponse(BaseModel):
    message: str
    user: UserResponse
    razorpay_order_id: Optional[str] = None
    razorpay_key_id: Optional[str] = None
    amount: Optional[int] = None
    currency: Optional[str] = None

class CreateSubscriptionOrderRequest(BaseModel):
    user_id: str = Field(..., max_length=36)
    selected_plan: str = Field("monthly", pattern="^(monthly|yearly)$")


class PaymentVerifyRequest(BaseModel):
    user_id: str = Field(..., max_length=36)
    razorpay_payment_id: str = Field(..., max_length=100)
    razorpay_order_id: str = Field(..., max_length=100)
    razorpay_signature: str = Field(..., max_length=200)
    
    # Optional registration details (used to write user to DB after payment)
    name: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, max_length=72)
    phone: Optional[str] = Field(None, max_length=20)
    company_logo: Optional[str] = Field(None, max_length=LOGO_MAX)
    selected_plan: Optional[str] = Field(None, pattern="^(monthly|yearly)$")



class UserRoleUpdate(BaseModel):
    role: str = Field(..., pattern="^(Admin|Designer|Sales)$")
    access_start: Optional[datetime] = None
    access_end: Optional[datetime] = None
    name: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    company_logo: Optional[str] = Field(None, max_length=LOGO_MAX)
    general_terms: Optional[str] = Field(None, max_length=20000)

class UserProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    company_logo: Optional[str] = Field(None, max_length=LOGO_MAX)
    general_terms: Optional[str] = Field(None, max_length=20000)
    billing_terms: Optional[str] = Field(None, max_length=20000)
    signature: Optional[str] = Field(None, max_length=LOGO_MAX)
    stamp: Optional[str] = Field(None, max_length=LOGO_MAX)
    billing_template: Optional[str] = Field(None, pattern=BILLING_TEMPLATE_PATTERN)
    new_password: Optional[str] = Field(None, min_length=6, max_length=72)

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class LoginTokenResponse(BaseModel):
    """Login/refresh return only the token; the client then calls GET /auth/me for the user."""
    access_token: str
    token_type: str

# --- CLIENT SCHEMAS ---
class ClientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    phone: str = Field(..., min_length=1, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = Field(None, max_length=2000)

class ClientUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=2000)

class ClientResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    name: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

# --- PROJECT SCHEMAS ---
class ProjectCreate(BaseModel):
    client_id: str
    name: str
    site_address: Optional[str] = None
    budget: float
    status: str = Field("Draft", pattern="^(Draft|Planning|Designing|Design Phase|Start Working|Execution|Completed|On Hold|Cancelled)$")

class ProjectUpdate(BaseModel):
    client_id: Optional[str] = None
    name: str
    site_address: Optional[str] = None
    budget: float
    status: str = Field("Draft", pattern="^(Draft|Planning|Designing|Design Phase|Start Working|Execution|Completed|On Hold|Cancelled)$")

class ProjectResponse(BaseModel):
    id: str
    client_id: str
    user_id: Optional[str] = None
    name: str
    site_address: Optional[str] = None
    budget: float
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

# --- ROOM SCHEMAS ---
class RoomCreate(BaseModel):
    project_id: str
    room_name: str

class RoomResponse(BaseModel):
    id: str
    project_id: str
    room_name: str
    created_at: datetime

    class Config:
        from_attributes = True

# --- CATALOG SCHEMAS ---
class CategoryResponse(BaseModel):
    id: str
    name: str
    is_general: bool = False
    created_at: datetime

    class Config:
        from_attributes = True

class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)

class CategoryUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)

class MyRateUpdate(BaseModel):
    base_rate: float = Field(..., ge=0.0, le=100_000_000)
    labor_cost: float = Field(0.0, ge=0.0, le=100_000_000)

class ItemResponse(BaseModel):
    id: str
    category_id: str
    subcategory: str
    name: str
    pricing_type: str
    brand: str
    base_rate: float
    labor_cost: float
    material: Optional[str] = None
    dimensions: Optional[str] = None
    is_general: bool = False
    has_custom_rate: bool = False
    created_at: datetime

    class Config:
        from_attributes = True

class ItemCreate(BaseModel):
    category_id: str = Field(..., max_length=36)
    subcategory: str = Field(..., max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    pricing_type: str = Field(..., pattern="^(sq_ft|running_ft|piece)$")
    brand: str = Field(..., max_length=255)
    base_rate: float = Field(..., ge=0.0, le=100_000_000)
    labor_cost: float = Field(0.0, ge=0.0, le=100_000_000)
    material: Optional[str] = Field(None, max_length=255)
    dimensions: Optional[str] = Field(None, max_length=255)

class ItemUpdate(BaseModel):
    category_id: Optional[str] = Field(None, max_length=36)
    subcategory: Optional[str] = Field(None, max_length=255)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    pricing_type: Optional[str] = Field(None, pattern="^(sq_ft|running_ft|piece)$")
    brand: Optional[str] = Field(None, max_length=255)
    base_rate: Optional[float] = Field(None, ge=0.0, le=100_000_000)
    labor_cost: Optional[float] = Field(None, ge=0.0, le=100_000_000)
    material: Optional[str] = Field(None, max_length=255)
    dimensions: Optional[str] = Field(None, max_length=255)

# --- QUOTATION ITEM SCHEMAS ---
class QuotationItemCreate(BaseModel):
    room_id: str
    category_id: str
    item_id: str
    qty: float = Field(..., ge=0.0, le=1_000_000)
    margin_percent: float = Field(0.0, ge=0.0, le=1000.0)
    gst_percent: float = Field(18.0, ge=0.0, le=100.0)
    remark: Optional[str] = Field(None, max_length=2000)
    length: Optional[float] = Field(1.0, ge=0.0, le=1_000_000)
    breadth: Optional[float] = Field(1.0, ge=0.0, le=1_000_000)
    height: Optional[float] = Field(1.0, ge=0.0, le=1_000_000)
    pricing_type: Optional[str] = None
    base_rate: Optional[float] = Field(None, ge=0.0)
    labor_cost: Optional[float] = Field(None, ge=0.0)

class QuotationItemResponse(BaseModel):
    id: str
    room_id: str
    room_name: Optional[str] = None
    category_id: str
    category_name: Optional[str] = None
    item_id: str
    item_name: Optional[str] = None
    item_brand: Optional[str] = None
    pricing_type: Optional[str] = None
    qty: float
    remark: Optional[str] = None
    length: Optional[float] = 1.0
    breadth: Optional[float] = 1.0
    height: Optional[float] = 1.0
    snapshot_rate: float
    snapshot_labor_cost: float
    snapshot_margin: float
    snapshot_gst_percent: float
    final_price: float
    total_amount: float
    created_at: datetime

    class Config:
        from_attributes = True

# --- QUOTATION SCHEMAS ---
class QuotationCreate(BaseModel):
    project_id: str
    discount_amount: float = Field(0.0, ge=0.0)
    terms_conditions: Optional[str] = Field(None, max_length=20000)
    items: List[QuotationItemCreate] = Field(..., max_length=500)
    apply_global_gst: Optional[bool] = False
    global_gst_percent: Optional[float] = 0.0

class QuotationResponse(BaseModel):
    id: str
    project_id: str
    project_name: Optional[str] = None
    client_name: Optional[str] = None
    site_address: Optional[str] = None
    version: int
    subtotal: float
    gst_amount: float
    discount_amount: float
    grand_total: float
    status: str
    terms_conditions: Optional[str] = None
    created_at: datetime
    items: List[QuotationItemResponse] = []

    class Config:
        from_attributes = True

class QuotationStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(draft|sent|approved|revised|rejected)$")

# --- PAYMENT SCHEMAS ---
class PaymentCreate(BaseModel):
    project_id: str
    amount: float = Field(..., gt=0.0)
    payment_method: str = Field(..., pattern="^(Cash|Wire Transfer)$")
    transaction_id: Optional[str] = None
    notes: Optional[str] = None
    payment_date: Optional[datetime] = None
    bill_id: Optional[str] = Field(None, max_length=36)

class PaymentResponse(BaseModel):
    id: str
    project_id: str
    project_name: Optional[str] = None
    amount: float
    payment_date: datetime
    payment_method: str
    transaction_id: Optional[str] = None
    notes: Optional[str] = None
    bill_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- BILLING SCHEMAS ---
MAX_BILL_ITEMS = 100

class BillItemIn(BaseModel):
    description: str = Field(..., min_length=1, max_length=200)
    qty: float = Field(..., gt=0, le=1_000_000)
    rate: float = Field(..., ge=0, le=100_000_000)
    tax_percent: float = Field(0.0, ge=0, le=100)
    # Same three pricing modes as estimations. `description` holds the item name.
    pricing_type: str = Field("piece", pattern="^(piece|sq_ft|running_ft)$")
    length: float = Field(1.0, ge=0, le=1_000_000)
    breadth: float = Field(1.0, ge=0, le=1_000_000)
    height: float = Field(1.0, ge=0, le=1_000_000)
    remark: Optional[str] = Field(None, max_length=500)

class BillItemOut(BillItemIn):
    id: str
    position: int
    amount: float

    class Config:
        from_attributes = True

class BillWrite(BaseModel):
    client_id: str = Field(..., max_length=36)
    project_id: str = Field(..., max_length=36)
    bill_date: date
    due_date: Optional[date] = None
    customer_gstin: Optional[str] = Field(None, max_length=20, pattern=r"^[0-9A-Za-z]*$")
    notes: Optional[str] = Field(None, max_length=2000)
    discount_amount: float = Field(0.0, ge=0, le=100_000_000)
    items: List[BillItemIn] = Field(..., min_length=1, max_length=MAX_BILL_ITEMS)

class BillResponse(BaseModel):
    id: str
    bill_number: str
    client_id: str
    client_name: Optional[str] = None
    project_id: str
    project_name: Optional[str] = None
    bill_date: date
    due_date: Optional[date] = None
    status: str
    template: str
    customer_gstin: Optional[str] = None
    notes: Optional[str] = None
    subtotal: float
    tax_amount: float
    discount_amount: float
    grand_total: float
    paid_amount: float = 0.0
    balance: float = 0.0
    payment_status: str = "unpaid"  # unpaid | partial | paid
    created_at: datetime
    items: List[BillItemOut] = []


# --- SUBSCRIPTION PAYMENT SCHEMAS ---
class SubscriptionPaymentResponse(BaseModel):
    id: str
    user_id: str
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: str
    razorpay_signature: Optional[str] = None
    plan: str
    amount: float
    access_start: datetime
    access_end: datetime
    created_at: datetime

    class Config:
        from_attributes = True


# --- PASSWORD RESET & EMAIL VERIFICATION SCHEMAS ---
class EmailVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    email: EmailStr
    token: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=6, max_length=72)

