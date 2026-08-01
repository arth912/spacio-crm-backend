from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional
from datetime import datetime

# --- AUTH SCHEMAS ---
class UserRegister(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)
    role: str = Field("Designer", pattern="^(Admin|Designer|Sales)$")
    phone: str = Field(..., min_length=10, max_length=15)
    company_logo: Optional[str] = None
    selected_plan: str = Field("monthly", pattern="^(monthly|yearly)$")

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: str
    phone: Optional[str] = None
    company_logo: Optional[str] = None
    general_terms: Optional[str] = None
    is_approved: bool
    created_at: datetime
    access_start: Optional[datetime] = None
    access_end: Optional[datetime] = None
    payment_status: str
    selected_plan: str


    class Config:
        from_attributes = True

class RegisterResponse(BaseModel):
    message: str
    user: UserResponse
    razorpay_order_id: Optional[str] = None
    razorpay_key_id: Optional[str] = None
    amount: Optional[int] = None
    currency: Optional[str] = None

class CreateSubscriptionOrderRequest(BaseModel):
    user_id: str
    selected_plan: str = Field("monthly", pattern="^(monthly|yearly)$")


class PaymentVerifyRequest(BaseModel):
    user_id: str
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    
    # Optional registration details (used to write user to DB after payment)
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    phone: Optional[str] = None
    company_logo: Optional[str] = None
    selected_plan: Optional[str] = None



class UserRoleUpdate(BaseModel):
    role: str = Field(..., pattern="^(Admin|Designer|Sales)$")
    access_start: Optional[datetime] = None
    access_end: Optional[datetime] = None
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    company_logo: Optional[str] = None
    general_terms: Optional[str] = None

class UserProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    company_logo: Optional[str] = None
    general_terms: Optional[str] = None
    new_password: Optional[str] = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

# --- CLIENT SCHEMAS ---
class ClientCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None

class ClientUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    address: Optional[str] = None

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
    status: str = Field("Draft", pattern="^(Draft|Planning|Designing|Design Phase|Start Working|Execution|Completed|On Hold)$")

class ProjectUpdate(BaseModel):
    client_id: Optional[str] = None
    name: str
    site_address: Optional[str] = None
    budget: float
    status: str = Field("Draft", pattern="^(Draft|Planning|Designing|Design Phase|Start Working|Execution|Completed|On Hold)$")

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
    created_at: datetime

    class Config:
        from_attributes = True

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
    created_at: datetime

    class Config:
        from_attributes = True

class ItemCreate(BaseModel):
    category_id: str
    subcategory: str
    name: str
    pricing_type: str = Field(..., pattern="^(sq_ft|running_ft|piece)$")
    brand: str
    base_rate: float = Field(..., ge=0.0)
    labor_cost: float = Field(0.0, ge=0.0)
    material: Optional[str] = None
    dimensions: Optional[str] = None

# --- QUOTATION ITEM SCHEMAS ---
class QuotationItemCreate(BaseModel):
    room_id: str
    category_id: str
    item_id: str
    qty: float
    margin_percent: float = Field(0.0, ge=0.0)
    gst_percent: float = Field(18.0, ge=0.0)
    remark: Optional[str] = None
    length: Optional[float] = 1.0
    breadth: Optional[float] = 1.0
    height: Optional[float] = 1.0
    pricing_type: Optional[str] = None
    base_rate: Optional[float] = None
    labor_cost: Optional[float] = None

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
    terms_conditions: Optional[str] = None
    items: List[QuotationItemCreate]
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

class PaymentResponse(BaseModel):
    id: str
    project_id: str
    amount: float
    payment_date: datetime
    payment_method: str
    transaction_id: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


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
    code: str

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    email: EmailStr
    token: str
    new_password: str = Field(..., min_length=6)

