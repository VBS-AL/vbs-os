from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base
from app.models.labor import BillingDept

class UserRole(str, enum.Enum):
    owner           = "owner"
    ops_manager     = "ops_manager"
    shop_foreman    = "shop_foreman"
    estimator       = "estimator"
    receiving_lead  = "receiving_lead"
    driver          = "driver"
    counter_staff   = "counter_staff"
    fitter          = "fitter"
    fitter_welder   = "fitter_welder"
    welder          = "welder"
    saw             = "saw"
    shear_brake     = "shear_brake"
    summer_welder   = "summer_welder"
    winter_welder   = "winter_welder"

class User(Base):
    __tablename__ = "users"

    id              = Column(Integer, primary_key=True, index=True)
    employee_id     = Column(String, unique=True, index=True)  # e.g. EMP-001
    first_name      = Column(String, nullable=False)
    last_name       = Column(String, nullable=True)   # nullable for TBD staff
    email           = Column(String, unique=True, nullable=True)
    hashed_password = Column(String, nullable=False)
    role            = Column(SAEnum(UserRole), nullable=False)
    is_active            = Column(Boolean, default=True)
    mobile_access        = Column(Boolean, default=False)
    default_billing_dept = Column(SAEnum(BillingDept), nullable=True)  # employee's home dept
    hourly_cost_rate     = Column(Float, nullable=True)                 # internal $/hr cost (owner-only)
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), onupdate=func.now())

    labor_entries    = relationship("LaborEntry", back_populates="employee")
    assigned_stages  = relationship("ProductionStage", foreign_keys="ProductionStage.assigned_to_id", back_populates="assigned_to")
    work_sessions    = relationship("WorkSession", back_populates="employee")
