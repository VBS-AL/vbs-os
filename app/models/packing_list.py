import enum
from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class ShippedVia(str, enum.Enum):
    vbs_delivery        = "vbs_delivery"
    customer_pickup     = "customer_pickup"
    third_party_freight = "third_party_freight"
    courier             = "courier"
    other               = "other"

SHIPPED_VIA_LABELS = {
    ShippedVia.vbs_delivery:        "VBS Delivery",
    ShippedVia.customer_pickup:     "Customer Pickup",
    ShippedVia.third_party_freight: "3rd Party Freight",
    ShippedVia.courier:             "Courier (UPS/FedEx/etc.)",
    ShippedVia.other:               "Other",
}


class PackingList(Base):
    __tablename__ = "packing_lists"

    id                = Column(Integer, primary_key=True, index=True)
    pl_number         = Column(String, unique=True, nullable=True, index=True)  # e.g. VBS-PL-26-00001
    order_id          = Column(Integer, ForeignKey("orders.id"), nullable=False, unique=True)

    # Shipping method
    shipped_via       = Column(SAEnum(ShippedVia), nullable=True)
    shipped_via_other = Column(String, nullable=True)   # free text when shipped_via == other
    date_shipped      = Column(Date, nullable=True)

    # Addresses (free text — editable, pre-populated from customer)
    ship_to           = Column(Text, nullable=True)
    sold_to           = Column(Text, nullable=True)

    # Contact
    contact_name      = Column(String, nullable=True)
    contact_phone     = Column(String, nullable=True)

    # Footer fields
    cartons           = Column(Integer, nullable=True)
    total_weight      = Column(Float, nullable=True)    # lbs; auto-calc if weights set on inventory
    order_complete    = Column(Boolean, default=False)
    balance_to_follow = Column(Boolean, default=False)
    packed_by         = Column(String, nullable=True)
    checked_by        = Column(String, nullable=True)   # display name (denormalised)
    checker_id        = Column(Integer, ForeignKey("users.id"), nullable=True)
    check_confirmed   = Column(Boolean, default=False)
    check_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    notes             = Column(Text, nullable=True)

    # Proof of delivery photo (Amazon/FedEx-style drop-off confirmation)
    delivery_photo_path = Column(String, nullable=True)  # relative path under app/static/

    # Meta
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    created_by_id     = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    order             = relationship("Order", back_populates="packing_list")
    created_by        = relationship("User", foreign_keys=[created_by_id])
    checker           = relationship("User", foreign_keys=[checker_id])
