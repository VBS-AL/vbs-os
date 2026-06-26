from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum, Text, ForeignKey, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class PaymentStatus(str, enum.Enum):
    unpaid   = "unpaid"
    partial  = "partial"
    paid     = "paid"
    overdue  = "overdue"
    void     = "void"

class PaymentMethod(str, enum.Enum):
    cash    = "cash"
    check   = "check"
    card    = "card"
    ach     = "ach"
    other   = "other"

class Invoice(Base):
    __tablename__ = "invoices"

    id              = Column(Integer, primary_key=True, index=True)
    # Invoice number always equals Order number per VBS convention
    invoice_number  = Column(String, unique=True, index=True)
    order_id        = Column(Integer, ForeignKey("orders.id"), nullable=False)
    invoice_date    = Column(Date, nullable=False)
    due_date        = Column(Date, nullable=True)
    payment_status  = Column(SAEnum(PaymentStatus), default=PaymentStatus.unpaid)
    subtotal        = Column(Float, nullable=False, default=0)
    tax             = Column(Float, nullable=False, default=0)
    total           = Column(Float, nullable=False, default=0)
    amount_paid     = Column(Float, default=0)
    balance_due     = Column(Float, default=0)
    notes           = Column(Text, nullable=True)
    created_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    order           = relationship("Order", back_populates="invoice")
    payments        = relationship("Payment", back_populates="invoice", cascade="all, delete-orphan")

class Payment(Base):
    __tablename__ = "payments"

    id              = Column(Integer, primary_key=True, index=True)
    invoice_id      = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    amount          = Column(Float, nullable=False)
    method          = Column(SAEnum(PaymentMethod), nullable=False)
    payment_date    = Column(Date, nullable=False)
    reference       = Column(String, nullable=True)  # check #, transaction ID
    notes           = Column(Text, nullable=True)
    recorded_by_id  = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    invoice         = relationship("Invoice", back_populates="payments")
