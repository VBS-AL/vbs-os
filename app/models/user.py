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
    is_active            = Column(Boolean, default