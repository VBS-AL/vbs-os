from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum, Text, ForeignKey, Boolean, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class InventoryCategory(str, enum.Enum):
    # ── Carbon Steel ─────────────────────────────────────────────────────────
    steel_pipe           = "steel_pipe"           # Schedule 40 Pipe, DOM
    steel_rect_tube      = "steel_rect_tube"      # Rectangular Tubing
    steel_sq_tube        = "steel_sq_tube"        # Square Tubing
    steel_channel        = "steel_channel"        # Channel - Steel
    steel_bar            = "steel_bar"            # HR/CR Bar (Flat/Sq/Rd), HR Dry Strip, Angle Iron
    steel_ibeam          = "steel_ibeam"          # I-Beam - Steel
    wide_flange          = "wide_flange"          # Wide Flange Beams
    columns              = "columns"              # Columns (N/S)
    steel_plate          = "steel_plate"          # Plate — AR / Floor / Hot Rolled
    steel_sheet          = "steel_sheet"          # Steel Sheet (CR/HR), Galvanized Sheet
    # ── Aluminum ─────────────────────────────────────────────────────────────
    aluminum_structural  = "aluminum_structural"  # Al Angle / Tubing / Channel / Round / Square / Flat
    aluminum_sheet       = "aluminum_sheet"       # Aluminum Sheet (Standard, Tread Brite)
    # ── Stainless ─────────────────────────────────────────────────────────────
    stainless_structural = "stainless_structural" # SS Round / Square bar
    stainless_sheet      = "stainless_sheet"      # SS Sheet — #4 / 2B / STD
    # ── Other ────────────────────────────────────────────────────────────────
    misc                 = "misc"                 # Decking, Exp. Metal, Grip Struts, Bar Grating, Rebar, Wire
    bumper_posts         = "bumper_posts"         # Bumper Posts (L-D, H-D)
    consumables          = "consumables"          # Welding wire, gas, grinding discs, paint drums, bandsaw blades
    hardware             = "hardware"             # Bolts, nuts, anchors, fasteners
    retail               = "retail"               # Walk-in retail items (3× cost markup)


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
    po_number     = Column(String