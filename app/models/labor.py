from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum, Text, ForeignKey, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class BillingDept(str, enum.Enum):
    general_labor       = "general_labor"        # $80/hr
    steel_fabrication   = "steel_fabrication"    # $100/hr
    aluminum_structural = "aluminum_structural"  # $120/hr
    hot_walk_in         = "hot_walk_in"          # $150/hr — walk-in rush
    welding_truck       = "welding_truck"        # $120/hr — portable welding truck on-site

BILLING_RATES = {
    BillingDept.general_labor:       80.0,
    BillingDept.steel_fabrication:   100.0,
    BillingDept.aluminum_structural: 120.0,
    BillingDept.hot_walk_in:         150.0,
    BillingDept.welding_truck:       120.0,
}

class LaborEntry(Base):
    __tablename__ = "labor_entries"

    id              = Column(Integer, primary_key=True, index=True)
    order_id        = Column(Integer, ForeignKey("orders.id"), nullable=False)
    stage_id        = Column(Integer, ForeignKey("production_stages.id"), nullable=True)
    employee_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    billing_dept    = Column(SAEnum(BillingDept), nullable=False)
    hours           = Column(Float, nullable=False)
    billing_rate    = Column(Float, nullable=False)
    billed_value    = Column(Float, nullable=False)
    work_date       = Column(Date, nullable=False)
    is_rework       = Column(Integer, default=0)
    notes           = Column(Text, nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    order           = relationship("Order", back_populates="labor_entries")
    employee        = relationship("User", back_populates="labor_entries")
    stage           = relationship("ProductionStage", foreign_keys=[stage_id])
