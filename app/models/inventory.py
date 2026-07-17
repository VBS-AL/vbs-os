from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum, Text, ForeignKey, Boolean, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class InventoryCategory(str, enum.Enum):
    # ── Carbon Steel — Tube & Pipe ────────────────────────────────────────────
    steel_pipe           = "steel_pipe"           # Schedule 40 Pipe
    steel_rect_tube      = "steel_rect_tube"      # Rectangular Tubing
    steel_sq_tube        = "steel_sq_tube"        # Square Tubing
    steel_dom_tube       = "steel_dom_tube"       # DOM Round Tube
    # ── Carbon Steel — Structural ─────────────────────────────────────────────
    steel_channel        = "steel_channel"        # Channel
    steel_angle          = "steel_angle"          # Equal Leg Angle
    steel_angle_unequal  = "steel_angle_unequal"  # Unequal Leg Angle
    steel_ibeam          = "steel_ibeam"          # I-Beam
    steel_wide_flange    = "steel_wide_flange"    # Wide Flange Beams
    steel_tstock         = "steel_tstock"         # T-Stock
    steel_columns        = "steel_columns"        # Columns (N/S)
    # ── Carbon Steel — Bar ────────────────────────────────────────────────────
    steel_flat_bar       = "steel_flat_bar"       # Flat Bar (HR/CR)
    steel_round_bar      = "steel_round_bar"      # Round Bar (HR/CR)
    steel_square_bar     = "steel_square_bar"     # Square Bar (HR/CR)
    steel_strip_hr       = "steel_strip_hr"       # HR Dry Strip
    # ── Carbon Steel — Plate & Sheet ─────────────────────────────────────────
    steel_plate_a36      = "steel_plate_a36"      # Plate A36
    steel_plate_ar400    = "steel_plate_ar400"    # Plate AR400
    steel_floor_plate    = "steel_floor_plate"    # Floor Plate (tread)
    steel_sheet_hr       = "steel_sheet_hr"       # Sheet HR
    steel_sheet_cr       = "steel_sheet_cr"       # Sheet CR
    steel_sheet_galv     = "steel_sheet_galv"     # Sheet Galvanized
    steel_sheet_perf     = "steel_sheet_perf"     # Sheet Perforated
    # ── Carbon Steel — Misc Shapes ────────────────────────────────────────────
    steel_expanded       = "steel_expanded"       # Expanded Metal
    steel_grip_strut     = "steel_grip_strut"     # Grip Strut (steel)
    steel_bar_grating    = "steel_bar_grating"    # Bar Grating
    steel_decking        = "steel_decking"        # Floor Decking
    steel_rebar          = "steel_rebar"          # Rebar
    # ── Aluminum — Structural ─────────────────────────────────────────────────
    alum_angle           = "alum_angle"           # Alum Equal Leg Angle
    alum_angle_unequal   = "alum_angle_unequal"   # Alum Unequal Leg Angle
    alum_channel         = "alum_channel"         # Alum Channel
    alum_flat_bar        = "alum_flat_bar"        # Alum Flat Bar
    alum_round           = "alum_round"           # Alum Round Bar
    alum_square_bar      = "alum_square_bar"      # Alum Square Bar
    alum_sq_tube         = "alum_sq_tube"         # Alum Square Tube
    alum_grip_strut      = "alum_grip_strut"      # Alum Grip Strut
    # ── Aluminum — Sheet ─────────────────────────────────────────────────────
    alum_sheet           = "alum_sheet"           # Alum Sheet (3003/5052/6061)
    alum_treadbrite      = "alum_treadbrite"      # Alum Tread Brite Sheet
    # ── Stainless ─────────────────────────────────────────────────────────────
    ss_round_bar         = "ss_round_bar"         # SS Round Bar
    ss_square_bar        = "ss_square_bar"        # SS Square Bar
    ss_sheet             = "ss_sheet"             # SS Sheet (#4 / 2B / STD)
    # ── Hardware ─────────────────────────────────────────────────────────────
    hardware_fasteners   = "hardware_fasteners"   # Nuts, washers, anchors, rods, studs, elbows
    hardware_caps        = "hardware_caps"        # Sq steel caps, dome caps
    hardware_gussets     = "hardware_gussets"     # Gusset plates
    hardware_base_plates = "hardware_base_plates" # Base plates
    hardware_handrail    = "hardware_handrail"    # Handrail fittings (elbows, covers)
    hardware_hinges      = "hardware_hinges"      # Hinges (butt, piano)
    # ── Other ────────────────────────────────────────────────────────────────
    bumper_posts         = "bumper_posts"         # Bumper Posts (L-D, H-D)
    consumables          = "consumables"          # Welding wire, gas, grinding discs, etc.
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
    weight_per_unit   = Column(Float, nullable=True)             # lbs per unit (for packing list auto-weight)
    retail_markup     = Column(Float, nullable=True, default=3.0) # sell price multiplier for retail orders (default 3×)
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
    order_id       = Column(Integer, ForeignKey("orders.id"), nullable=True)  #