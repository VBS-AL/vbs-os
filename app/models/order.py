from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum, Text, Float, ForeignKey, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class JobType(str, enum.Enum):
    walk_in     = "walk_in"
    fabrication = "fabrication"
    structural  = "structural"
    beam        = "beam"        # residential beam jobs — labor rolled into $/lb price
    retail      = "retail"      # counter/material sale — line items auto-priced at cost × retail_markup

class OrderStatus(str, enum.Enum):
    draft         = "draft"
    confirmed     = "confirmed"
    in_production = "in_production"
    on_hold       = "on_hold"
    qa_review     = "qa_review"
    ready         = "ready"
    delivered     = "delivered"
    invoiced      = "invoiced"
    paid          = "paid"
    cancelled     = "cancelled"

class Priority(str, enum.Enum):
    standard     = "standard"
    priority     = "priority"
    urgent       = "urgent"
    hot_walk_in  = "hot_walk_in"  # walk-in rush — billed at $150/hr

class StagingLocation(str, enum.Enum):
    shop          = "shop"
    outgoing_area = "outgoing_area"
    stock_room    = "stock_room"

class Order(Base):
    __tablename__ = "orders"

    id                  = Column(Integer, primary_key=True, index=True)
    order_number        = Column(String, unique=True, index=True)
    customer_id         = Column(Integer, ForeignKey("customers.id"), nullable=False)
    quote_id            = Column(Integer, ForeignKey("quotes.id"), nullable=True)
    job_type            = Column(SAEnum(JobType), nullable=False)
    status              = Column(SAEnum(OrderStatus), default=OrderStatus.confirmed)
    priority            = Column(SAEnum(Priority), default=Priority.standard)
    description         = Column(Text, nullable=True)
    drawings_required   = Column(Boolean, default=False)
    drawing_file        = Column(String, nullable=True)   # stored filename under app/static/drawings/
    customer_po         = Column(String, nullable=True)   # customer's PO number
    paint_spec                  = Column(String, nullable=True)
    preferred_delivery_method   = Column(String, nullable=True)  # customer_pickup | delivery | None
    promised_date               = Column(Date, nullable=True)
    ship_date           = Column(Date, nullable=True)
    hold_reason         = Column(Text, nullable=True)
    hold_owner          = Column(String, nullable=True)
    previous_status     = Column(SAEnum(OrderStatus), nullable=True)
    staging_location    = Column(SAEnum(StagingLocation), nullable=True)
    rework_count        = Column(Integer, default=0)
    notification_sent   = Column(Boolean, default=False)
    notification_method = Column(String, nullable=True)
    notes               = Column(Text, nullable=True)
    created_by_id       = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())

    customer            = relationship("Customer", back_populates="orders")
    quote               = relationship("Quote", back_populates="order")
    line_items          = relationship("OrderLineItem", back_populates="order", cascade="all, delete-orphan")
    production_stages   = relationship("ProductionStage", back_populates="order", cascade="all, delete-orphan")
    labor_entries       = relationship("LaborEntry", back_populates="order")
    qa_records          = relationship("QARecord", back_populates="order")
    drawing_records     = relationship("DrawingRecord", back_populates="order")
    invoice             = relationship("Invoice", back_populates="order", uselist=False)
    packing_list        = relationship("PackingList", back_populates="order", uselist=False)
    scrap_records       = relationship("ScrapRecord", back_populates="order")
    remnant_records     = relationship("RemnantRecord", back_populates="order")
    work_sessions       = relationship("WorkSession", back_populates="order")

class OrderLineItem(Base):
    __tablename__ = "order_line_items"

    id                    = Column(Integer, primary_key=True, index=True)
    order_id              = Column(Integer, ForeignKey("orders.id"), nullable=False)
    line_number           = Column(Integer, nullable=False)
    description           = Column(Text, nullable=False)
    quantity              = Column(Float, nullable=False, default=1)
    unit                  = Column(String, nullable=True)
    material              = Column(String, nullable=True)
    paint_override        = Column(String, nullable=True)
    unit_price            = Column(Float, nullable=True)
    notes                 = Column(Text, nullable=True)
    internal_notes        = Column(Text, nullable=True)    # shop/production notes — never shown to customer
    inventory_item_id     = Column(Integer, ForeignKey("inventory_items.id"), nullable=True)
    estimated_labor_hours = Column(Float, nullable=True)
    estimated_labor_dept  = Column(String, nullable=True)
    labor_rate_snapshot   = Column(Float, nullable=True)   # $/hr rate FROZEN at time of creation
    is_delivery_surcharge = Column(Boolean, default=False)
    # 3rd party outsourced services (laser cuts, galvanizing, etc.)
    third_party_cost      = Column(Float, nullable=True)   # what we paid the vendor
    third_party_markup    = Column(Float, nullable=True)   # decimal, e.g. 0.30 = 30%

    order                 = relationship("Order", back_populates="line_items")
    inventory_item        = relationship("InventoryItem")
