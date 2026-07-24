from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum, Text, ForeignKey, Date, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class ScrapDisposition(str, enum.Enum):
    display  = "display"    # goes to retail walk-up display
    bulk     = "bulk"       # sold as bulk scrap
    recycle  = "recycle"    # recycled

class RemnantDisposition(str, enum.Enum):
    back_to_stock = "back_to_stock"   # returns to inventory
    retail        = "retail"          # goes to retail display
    discard       = "discard"         # too small / no value

class DisplayStatus(str, enum.Enum):
    available = "available"
    on_hold   = "on_hold"
    sold      = "sold"

class ScrapRecord(Base):
    __tablename__ = "scrap_records"

    id              = Column(Integer, primary_key=True, index=True)
    order_id        = Column(Integer, ForeignKey("orders.id"), nullable=True)
    material_type   = Column(String, nullable=False)    # Steel, Aluminum, etc.
    weight_lbs      = Column(Float, nullable=True)      # from shop scale
    disposition     = Column(SAEnum(ScrapDisposition), nullable=False)
    recovery_value  = Column(Float, nullable=True)
    scrap_date      = Column(Date, nullable=False)
    notes           = Column(Text, nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    order           = relationship("Order", back_populates="scrap_records")

class RemnantRecord(Base):
    """Leftover material logged during QA close-out."""
    __tablename__ = "remnant_records"

    id                  = Column(Integer, primary_key=True, index=True)
    order_id            = Column(Integer, ForeignKey("orders.id"), nullable=False)
    line_item_id        = Column(Integer, ForeignKey("order_line_items.id"), nullable=True)
    inventory_item_id   = Column(Integer, ForeignKey("inventory_items.id"), nullable=True)
    description         = Column(String, nullable=False)       # copy of line item desc
    qty_remaining       = Column(Float, nullable=False)
    unit                = Column(String, nullable=True)
    unit_cost           = Column(Float, nullable=True)         # cost value of the remnant
    disposition         = Column(SAEnum(RemnantDisposition), nullable=False)
    logged_by_id        = Column(Integer, ForeignKey("users.id"), nullable=True)
    logged_at           = Column(DateTime(timezone=True), server_default=func.now())
    notes               = Column(Text, nullable=True)

    order               = relationship("Order", back_populates="remnant_records")
    line_item           = relationship("OrderLineItem")
    inventory_item      = relationship("InventoryItem")
    logged_by           = relationship("User")


class RetailScrapItem(Base):
    __tablename__ = "retail_scrap_items"

    id              = Column(Integer, primary_key=True, index=True)
    description     = Column(String, nullable=False)
    material_type   = Column(String, nullable=False)
    retail_price    = Column(Float, nullable=False)
    status          = Column(SAEnum(DisplayStatus), default=DisplayStatus.available)
    date_added      = Column(Date, nullable=False)
    date_sold       = Column(Date, nullable=True)
    sale_price      = Column(Float, nullable=True)
    notes           = Column(Text, nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
