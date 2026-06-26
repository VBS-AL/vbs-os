from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum, Text, Float, ForeignKey, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class QuoteStatus(str, enum.Enum):
    draft       = "draft"
    sent        = "sent"
    under_review = "under_review"
    converted   = "converted"
    declined    = "declined"
    expired     = "expired"

class Quote(Base):
    __tablename__ = "quotes"

    id              = Column(Integer, primary_key=True, index=True)
    quote_number    = Column(String, unique=True, index=True)  # VBS-Q-YY-#####
    customer_id     = Column(Integer, ForeignKey("customers.id"), nullable=False)
    job_type        = Column(String, nullable=False)
    status          = Column(SAEnum(QuoteStatus), default=QuoteStatus.draft)
    description     = Column(Text, nullable=True)
    valid_until     = Column(Date, nullable=True)
    decline_reason  = Column(String, nullable=True)
    total_estimated = Column(Float, nullable=True)
    created_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    customer        = relationship("Customer", back_populates="quotes")
    line_items      = relationship("QuoteLineItem", back_populates="quote", cascade="all, delete-orphan")
    order           = relationship("Order", back_populates="quote", uselist=False)

class QuoteLineItem(Base):
    __tablename__ = "quote_line_items"

    id              = Column(Integer, primary_key=True, index=True)
    quote_id        = Column(Integer, ForeignKey("quotes.id"), nullable=False)
    line_number     = Column(Integer, nullable=False)
    description     = Column(Text, nullable=False)
    quantity        = Column(Float, default=1)
    unit            = Column(String, nullable=True)
    material        = Column(String, nullable=True)
    est_labor_hours = Column(Float, nullable=True)
    billing_dept    = Column(String, nullable=True)  # General / Steel Fab / Structural
    unit_price      = Column(Float, nullable=True)
    notes           = Column(Text, nullable=True)

    quote           = relationship("Quote", back_populates="line_items")
