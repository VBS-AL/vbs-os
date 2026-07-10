from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum, Text, ForeignKey, Boolean, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class InventoryCategory(str, enum.Enum):
    plate      = "plate"       # flat plate steel — Plate area
    structural = "structural"  # angle iron, tube, channel — Structural Steel area
    beam       = "beam"        # I-beam, H-beam, wide flange — Beam area
    consumables = "consumables" # welding wire, gas, paint, grinding discs
    hardware   = "hardware"    # bolts, nuts, anchors, fasteners


class AdjustmentReason(str, enum.Enum):
    received   = "received"    # stock in from supplier
    used       = "used"        # consumed in production
    damaged    = "damaged"     # damaged / scrapped
    correction = "correction"  # physical count correction
    returned   = "returned"    # returned to supplier
    other      = "other"


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id                = Column(Integer, primary_key=True, index=True)
    sku               = Column(String, unique=True, index=True)   # VBS-INV-00001
    name              = Column(String, nullable=False)
    category          = Column(SAEnum(InventoryCategory), nullable=False)
    description       = Column(Text, nullable=True)
    unit              = Column(String, nullable=False)            # lbs, ft, each, gal …
    quantity_on_hand  = Column(Float, default=0)
    reorder_threshold = Column(Float, nullable=True)             # alert below this
    cost_per_unit     = Column(Float, nullable=True)
    location          = Column(String, nullable=True)            # shelf / bin in shop
    supplier_name     = Column(String, nullable=True)
    supplier_contact  = Column(String, nullable=True)
    notes             = Column(Text, nullable=True)
    is_active         = Column(Boolean, default=True)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), onupdate=func.now())

    adjustments  = relationship("InventoryAdjustment", back_populates="item",
                                order_by="InventoryAdjustment.created_at.desc()")
    po_line_items = relationship("POLineItem", back_populates="item")


class InventoryAdjustment(Base):
    __tablename__ = "inventory_adjustments"

    id             = Column(Integer, primary_key=True, index=True)
    item_id        = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    delta          = Column(Float, nullable=False)   # + = add, − = remove
    reason         = Column(SAEnum(AdjustmentReason), nullable=False)
    order_id       = Column(Integer, ForeignKey("orders.id"), nullable=True)  # future linkage
    notes          = Column(Text, nullable=True)
    recorded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    item        = relationship("InventoryItem", back_populates="adjustments")
    order       = relationship("Order")
    recorded_by = relationship("User", foreign_keys=[recorded_by_id])


# ── Purchase Orders (kept for future PO workflow) ──────────────────────────

class POStatus(str, enum.Enum):
    ordered   = "ordered"
    partial   = "partial"
    received  = "received"
    cancelled = "cancelled"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id            = Column(Integer, primary_key=True, index=True)
    po_number     = Column(String, unique=True, index=True)   # VBS-P-YY-#####
    vendor        = Column(String, nullable=False)
    status        = Column(SAEnum(POStatus), default=POStatus.ordered)
    order_date    = Column(Date, nullable=False)
    expected_date = Column(Date, nullable=True)
    received_date = Column(Date, nullable=True)
    notes         = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    line_items      = relationship("POLineItem", back_populates="po", cascade="all, delete-orphan")
    outside_service = relationship("OutsideService", back_populates="po", uselist=False)


class POLineItem(Base):
    __tablename__ = "po_line_items"

    id           = Column(Integer, primary_key=True, index=True)
    po_id        = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    item_id      = Column(Integer, ForeignKey("inventory_items.id"), nullable=True)
    description  = Column(String, nullable=False)
    qty_ordered  = Column(Float, nullable=False)
    qty_received = Column(Float, default=0)
    unit_cost    = Column(Float, nullable=True)
    received_date = Column(Date, nullable=True)

    po   = relationship("PurchaseOrder", back_populates="line_items")
    item = relationship("InventoryItem", back_populates="po_line_items")


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

    po = relationship("PurchaseOrder", back_populates="outside_service")
