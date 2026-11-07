from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, ForeignKey, DateTime, Text, Boolean, desc
from sqlalchemy.orm import relationship
from db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False) # 'Admin', 'Designer', 'Sales'
    phone = Column(String(50), nullable=True)
    is_approved = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_email_verified = Column(Boolean, default=False, nullable=False)
    token_version = Column(Integer, default=0, nullable=False, server_default="0")

    subscription_payments = relationship(
        "SubscriptionPayment", back_populates="user",
        cascade="all, delete-orphan",
        order_by="desc(SubscriptionPayment.created_at)"
    )

    # One-to-one satellite tables (split out of the old wide `users` row).
    # subscription + profile are eager-joined so API responses (UserResponse) can read them;
    # use .options(lazyload(User.profile)) for hot paths that don't need the logo blob.
    # auth_tokens is lazy="raise": load it explicitly with selectinload(User.auth_tokens).
    subscription = relationship(
        "UserSubscription", uselist=False, back_populates="user",
        cascade="all, delete-orphan", lazy="joined",
    )
    profile = relationship(
        "UserProfile", uselist=False, back_populates="user",
        cascade="all, delete-orphan", lazy="joined",
    )
    auth_tokens = relationship(
        "UserAuthTokens", uselist=False, back_populates="user",
        cascade="all, delete-orphan", lazy="raise",
    )

    # --- Backwards-compatible attribute access (reads default sensibly if the row is missing) ---
    def _sub(self, create=False):
        if self.subscription is None and create:
            self.subscription = UserSubscription()
        return self.subscription

    def _prof(self, create=False):
        if self.profile is None and create:
            self.profile = UserProfile()
        return self.profile

    @property
    def company_logo(self):
        return self.profile.company_logo if self.profile else None

    @company_logo.setter
    def company_logo(self, value):
        self._prof(create=True).company_logo = value

    @property
    def general_terms(self):
        return self.profile.general_terms if self.profile else None

    @general_terms.setter
    def general_terms(self, value):
        self._prof(create=True).general_terms = value

    @property
    def payment_status(self):
        return self.subscription.payment_status if self.subscription and self.subscription.payment_status else "unpaid"

    @payment_status.setter
    def payment_status(self, value):
        self._sub(create=True).payment_status = value

    @property
    def selected_plan(self):
        return self.subscription.selected_plan if self.subscription and self.subscription.selected_plan else "monthly"

    @selected_plan.setter
    def selected_plan(self, value):
        self._sub(create=True).selected_plan = value

    @property
    def access_start(self):
        return self.subscription.access_start if self.subscription else None

    @access_start.setter
    def access_start(self, value):
        self._sub(create=True).access_start = value

    @property
    def access_end(self):
        return self.subscription.access_end if self.subscription else None

    @access_end.setter
    def access_end(self, value):
        self._sub(create=True).access_end = value


class UserProfile(Base):
    """Company branding shown on quotation PDFs (large base64 logo + terms)."""
    __tablename__ = "user_profiles"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    company_logo = Column(Text, nullable=True)
    general_terms = Column(Text, nullable=True)

    user = relationship("User", back_populates="profile")


class UserSubscription(Base):
    """Current access state. Source of truth for login checks (also covers admin-granted access
    with no payment). subscription_payments remains the payment history."""
    __tablename__ = "user_subscriptions"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    payment_status = Column(String(50), default="unpaid", nullable=False)
    selected_plan = Column(String(50), default="monthly", nullable=False)
    access_start = Column(DateTime(timezone=False), nullable=True)
    access_end = Column(DateTime(timezone=False), nullable=True)

    user = relationship("User", back_populates="subscription")


class UserAuthTokens(Base):
    """Short-lived OTP / reset state. Separate attempt counters per flow."""
    __tablename__ = "user_auth_tokens"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    verification_token = Column(String(128), nullable=True)
    verification_expires_at = Column(DateTime, nullable=True)
    verification_attempts = Column(Integer, default=0, nullable=False, server_default="0")
    reset_token = Column(String(128), nullable=True)
    reset_expires_at = Column(DateTime, nullable=True)
    reset_attempts = Column(Integer, default=0, nullable=False, server_default="0")

    user = relationship("User", back_populates="auth_tokens")


class Client(Base):
    __tablename__ = "clients"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=False)
    email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    projects = relationship("Project", back_populates="client", cascade="all, delete-orphan")

class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True)
    client_id = Column(String(36), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    site_address = Column(Text, nullable=True)
    budget = Column(Float, nullable=False)
    status = Column(String(50), nullable=False) # 'Planning', 'Design Phase', 'Execution', 'Completed', 'On Hold'
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="projects")
    rooms = relationship("Room", back_populates="project", cascade="all, delete-orphan")
    quotations = relationship("Quotation", back_populates="project", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="project", cascade="all, delete-orphan")

class Room(Base):
    __tablename__ = "rooms"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    room_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="rooms")
    quotation_items = relationship("QuotationItem", back_populates="room", cascade="all, delete-orphan")

class Category(Base):
    __tablename__ = "categories"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    # owner; every user has a private catalogue
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("Item", back_populates="category", cascade="all, delete-orphan")
    quotation_items = relationship("QuotationItem", back_populates="category")

class Item(Base):
    __tablename__ = "items"

    id = Column(String(36), primary_key=True)
    category_id = Column(String(36), ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True)
    subcategory = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    pricing_type = Column(String(50), nullable=False) # 'sq_ft', 'running_ft', 'piece'
    brand = Column(String(255), nullable=False)
    base_rate = Column(Float, nullable=False)
    labor_cost = Column(Float, default=0.0)
    material = Column(Text, nullable=True)
    dimensions = Column(String(255), nullable=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    category = relationship("Category", back_populates="items")
    quotation_items = relationship("QuotationItem", back_populates="item")

class Quotation(Base):
    __tablename__ = "quotations"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    subtotal = Column(Float, nullable=False, default=0.0)
    gst_amount = Column(Float, nullable=False, default=0.0)
    discount_amount = Column(Float, nullable=False, default=0.0)
    grand_total = Column(Float, nullable=False, default=0.0)
    status = Column(String(50), nullable=False, default="draft") # 'draft', 'sent', 'approved', 'revised', 'rejected'
    terms_conditions = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="quotations")
    items = relationship("QuotationItem", back_populates="quotation", cascade="all, delete-orphan")

class QuotationItem(Base):
    __tablename__ = "quotation_items"

    id = Column(String(36), primary_key=True)
    quotation_id = Column(String(36), ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True)
    room_id = Column(String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id = Column(String(36), ForeignKey("categories.id"), nullable=False, index=True)
    item_id = Column(String(36), ForeignKey("items.id"), nullable=False, index=True)
    qty = Column(Float, nullable=False)
    remark = Column(Text, nullable=True)
    
    length = Column(Float, default=1.0, nullable=True)
    breadth = Column(Float, default=1.0, nullable=True)
    height = Column(Float, default=1.0, nullable=True)
    pricing_type = Column(String(50), default="piece", nullable=True)
    
    # Store snapshots to ensure historically unchanged quotes
    snapshot_rate = Column(Float, nullable=False)
    snapshot_labor_cost = Column(Float, default=0.0)
    snapshot_margin = Column(Float, default=0.0)
    snapshot_gst_percent = Column(Float, default=18.0)
    
    # Pre-calculated fields for faster querying
    final_price = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    quotation = relationship("Quotation", back_populates="items")
    room = relationship("Room", back_populates="quotation_items")
    category = relationship("Category", back_populates="quotation_items")
    item = relationship("Item", back_populates="quotation_items")

class Payment(Base):
    __tablename__ = "payments"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    payment_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    payment_method = Column(String(50), nullable=False) # 'Bank Transfer', 'Cash', 'Cheque', 'UPI', 'Other'
    transaction_id = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="payments")


class SubscriptionPayment(Base):
    __tablename__ = "subscription_payments"

    id                  = Column(String(36), primary_key=True)
    user_id             = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    razorpay_order_id   = Column(String(255), nullable=True)
    razorpay_payment_id = Column(String(255), nullable=False)
    razorpay_signature  = Column(String(255), nullable=True)
    plan                = Column(String(50), nullable=False)   # 'monthly' | 'yearly'
    amount              = Column(Float, nullable=False)        # 499 or 4999
    access_start        = Column(DateTime, nullable=False)
    access_end          = Column(DateTime, nullable=False)
    created_at          = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="subscription_payments")
