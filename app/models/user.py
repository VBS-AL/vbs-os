from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class UserRole(str, enum.Enum):
    owner           = "owner"
    ops_manager     = "ops_manager"
    shop_foreman    = "shop_foreman"
    estimator       = "estimator"
    receiving_lead  = "receiving_lead"
    driver          = "driver"
    counter_staff   = "counter_staff"

class User(Base):
    __tablename__ = "users"

    id              = Column(Integer, primary_key=True, index=True)
    employee_id     = Column(String, unique=True, index=True)  # e.g. EMP-001
    first_name      = Column(String, nullable=False)
    last_name       = Column(String, nullable=True)   # nullable for TBD staff
    email           = Column(String, unique=True, nullable=True)
    hashed_password = Column(String, nullable=False)
    role            = Column(SAEnum(UserRole), nullable=False)
    is_active       = Column(Boolean, default=True)
    mobile_access   = Column(Boolean, default=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    labor_entries   = relationship("LaborEntry", back_populates="employee")
