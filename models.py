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
    company_logo = Column(Text, nullable=True)
    general_terms = Column(Text, nullable=True)
    is_approved = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    access_start = Column(DateTime(timezone=False), nullable=True)
    access_end = Column(DateTime(timezone=False), nullable=True)
    payment_status = Column(String(50), default="unpaid", nullable=False)
    selected_plan = Column(String(50), default="monthly", nullable=False)


    subscription_payments = relationship(
        "SubscriptionPayment", back_populates="user",
        cascade="all, delete-orphan",
        order_by="desc(SubscriptionPayment.created_at)"
    )


class Client(Base):
    __tablename__ = "clients"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=False)
    email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    projects = relationship("Project", back_populates="client", cascade="all, delete-orphan")

class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True)
    client_id = Column(String(36), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
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
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    room_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="rooms")
    quotation_items = relationship("QuotationItem", back_populates="room", cascade="all, delete-orphan")

class Category(Base):
    __tablename__ = "categories"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("Item", back_populates="category", cascade="all, delete-orphan")
    quotation_items = relationship("QuotationItem", back_populates="category")

class Item(Base):
    __tablename__ = "items"

    id = Column(String(36), primary_key=True)
    category_id = Column(String(36), ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    subcategory = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    pricing_type = Column(String(50), nullable=False) # 'sq_ft', 'running_ft', 'piece'
    brand = Column(String(255), nullable=False)
    base_rate = Column(Float, nullable=False)
    labor_cost = Column(Float, default=0.0)
    material = Column(Text, nullable=True)
    dimensions = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    category = relationship("Category", back_populates="items")
    quotation_items = relationship("QuotationItem", back_populates="item")

class Quotation(Base):
    __tablename__ = "quotations"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
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
    quotation_id = Column(String(36), ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False)
    room_id = Column(String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    category_id = Column(String(36), ForeignKey("categories.id"), nullable=False)
    item_id = Column(String(36), ForeignKey("items.id"), nullable=False)
    qty = Column(Float, nullable=False)
    remark = Column(Text, nullable=True)
    
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
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
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
