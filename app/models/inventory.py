from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum, Text, ForeignKey, Boolean, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class MaterialCategory(str, enum.Enum):
    steel        = "steel"
    aluminum     = "aluminum"
    hardware     = "hardware"
    consumables  = "consumables"
    other        = "other"

class POStatus(str, enum.Enum):
    ordered  = "ordered"
    partial  = "partial"
    received = "received"
    cancelled = "cancelled"

class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id              = Column(Integer, primary_key=True, index=True)
    item_number     = Column(String, unique=True, index=True)
    description     = Column(String, nullable=False)
    category        = Column(SAEnum(MaterialCategory), nullable=False)
    unit            = Column(String, nullable=True)       # ft, lbs, ea, etc.
    qty_on_hand     = Column(Float, default=0)
    qty_allocated   = Column(Float, default=0)            # reserved for open orders
    reorder_point   = Column(Float, nullable=True)
    preferred_vendor = Column(String, nullable=True)
    unit_cost       = Column(Float, nullable=True)        # last known cost
    location        = Column(String, nullable=True)       # shelf/bin location
    is_active       = Column(Boolean, default=True)
    notes           = Column(Text, nullable=True)
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    po_line_items   = relationship("POLineItem", back_populates="item")

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id              = Column(Integer, primary_key=True, index=True)
    po_number       = Column(String, unique=True, index=True)  # VBS-P-YY-#####
    vendor          = Column(String, nullable=False)
    status          = Column(SAEnum(POStatus), default=POStatus.ordered)
    order_date      = Column(Date, nullable=False)
    expected_date   = Column(Date, nullable=True)
    received_date   = Column(Date, nullable=True)
    notes           = Column(Text, nullable=True)
    created_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    line_items      = relationship("POLineItem", back_populates="po", cascade="all, delete-orphan")
    outside_service = relationship("OutsideService", back_populates="po", uselist=False)

class POLineItem(Base):
    __tablename__ = "po_line_items"

    id              = Column(Integer, primary_key=True, index=True)
    po_id           = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    item_id         = Column(Integer, ForeignKey("inventory_items.id"), nullable=True)
    description     = Column(String, nullable=False)
    qty_ordered     = Column(Float, nullable=False)
    qty_received    = Column(Float, default=0)
    unit_cost       = Column(Float, nullable=True)
    received_date   = Column(Date, nullable=True)

    po              = relationship("PurchaseOrder", back_populates="line_items")
    item            = relationship("InventoryItem", back_populates="po_line_items")

class OutsideService(Base):
    __tablename__ = "outside_services"

    id              = Column(Integer, primary_key=True, index=True)
    po_id           = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    order_id        = Column(Integer, ForeignKey("orders.id"), nullable=True)
    vendor          = Column(String, nullable=False)
    service_type    = Column(String, nullable=False)
    quoted_cost     = Column(Float, nullable=True)
    actual_cost     = Column(Float, nullable=True)
    sent_date       = Column(Date, nullable=True)
    expected_return = Column(Date, nullable=True)
    returned_date   = Column(Date, nullable=True)
    notes           = Column(Text, nullable=True)

    po              = relationship("PurchaseOrder", back_populates="outside_service")
