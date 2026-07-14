from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum, Text, Float, ForeignKey, Date, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class QuoteStatus(str, enum.Enum):
    draft       = "draft"
    sent        = "sent"
    accepted    = "accepted"
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
    priority        = Column(String, default="standard")
    paint_spec      = Column(String, nullable=True)
    drawings_required = Column(Boolean, default=False)
    drawing_file    = Column(String, nullable=True)   # stored filename under app/static/drawings/
    customer_po     = Column(String, nullable=True)   # customer's PO number
    description     = Column(Text, nullable=True)
    notes           = Column(Text, nullable=True)
    valid_until     = Column(Date, nullable=True)       # set when sent (sent_at + 14 days)
    sent_at         = Column(DateTime(timezone=True), nullable=True)
    decline_reason  = Column(String, nullable=True)
    total_estimated = Column(Float, nullable=True)
    created_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    revision        = Column(Integer, default=1, nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    customer        = relationship("Customer", back_populates="quotes")
    line_items      = relationship("QuoteLineItem", back_populates="quote", cascade="all, delete-orphan")
    order           = relationship("Order", back_populates="quote", uselist=False)
    revisions       = relationship("QuoteRevision", back_populates="quote", order_by="QuoteRevision.revision_number")

class QuoteLineItem(Base):
    __tablename__ = "quote_line_items"

    id              = Column(Integer, primary_key=True, index=True)
    quote_id        = Column(Integer, ForeignKey("quotes.id"), nullable=False)
    line_number     = Column(Integer, nullable=False)
    description     = Column(Text, nullable=False)
    quantity        = Column(Float, default=1)
    unit            = Column(String, nullable=True)
    material        = Column(String, nullable=True)
    unit_price      = Column(Float, nullable=True)
    paint_override        = Column(String, nullable=True)  # None = inherit job paint_spec
    notes                 = Column(Text, nullable=True)
    internal_notes        = Column(Text, nullable=True)    # shop/production notes — never shown to customer
    inventory_item_id     = Column(Integer, ForeignKey("inventory_items.id"), nullable=True)
    estimated_labor_hours = Column(Float, nullable=True)
    estimated_labor_dept  = Column(String, nullable=True)  # BillingDept value
    is_delivery_surcharge = Column(Boolean, default=False)

    quote           = relationship("Quote", back_populates="line_items")
    inventory_item  = relationship("InventoryItem")

class QuoteRevision(Base):
    __tablename__ = "quote_revisions"

    id              = Column(Integ