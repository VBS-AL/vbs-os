from sqlalchemy import Column, String, DateTime
from sqlalchemy.sql import func
from app.database import Base


class AppSetting(Base):
    """Simple key-value store for application-wide settings."""
    __tablename__ = "app_settings"

    key        = Column(String, primary_key=True)
    value      = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
