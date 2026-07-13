from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class StageType(str, enum.Enum):
    material_receiving  = "material_receiving"
    drawings            = "drawings"
    fabrication         = "fabrication"
    welding             = "welding"
    finishing           = "finishing"
    qa_qc               = "qa_qc"
    delivery            = "delivery"

class StageStatus(str, enum.Enum):
    pending     = "pending"
    in_progress = "in_progress"
    complete    = "complete"
    blocked     = "blocked"

class QAResult(str, enum.Enum):
    pass_result = "pass"
    fail        = "fail"
    rework      = "rework"
    conditional = "conditional"

class DrawingStatus(str, enum.Enum):
    pending      = "pending"
    under_review = "under_review"
    approved     = "approved"
    rejected     = "rejected"

class ProductionStage(Base):
    __tablename__ = "production_stages"

    id             = Column(Integer, primary_key=True, index=True)
    order_id       = Column(Integer, ForeignKey("orders.id"), nullable=False)
    stage_type     = Column(SAEnum(StageType), nullable=False)
    status         = Column(SAEnum(StageStatus), default=StageStatus.pending)
    assigned_to_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    started_at     = Column(DateTime(timezone=True), nullable=True)
    completed_at   = Column(DateTime(timezone=True), nullable=True)
    notes          = Column(Text, nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    order          = relationship("Order", back_populates="production_stages")
    assigned_to    = relationship("User", foreign_keys=[assigned_to_id], back_populates="assigned_stages")
    work_sessions  = relationship("WorkSession", back_populates="stage")

class QARecord(Base):
    __tablename__ = "qa_records"

    id             = Column(Integer, primary_key=True, index=True)
    order_id       = Column(Integer, ForeignKey("orders.id"), nullable=False)
    inspector_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    result         = Column(SAEnum(QAResult), nullable=False)
    failure_reason = Column(Text, nullable=True)
    rework_notes   = Column(Text, nullable=True)
    certified_weld = Column(Boolean, default=False)
    cert_reference = Column(String, nullable=True)
    inspected_at   = Column(DateTime(timezone=True), server_default=func.now())

    order          = relationship("Order", back_populates="qa_records")
    inspector      = relationship("User", foreign_keys=[inspector_id])

class DrawingRecord(Base):
    __tablename__ = "drawing_records"

    id             = Column(Integer, primary_key=True, index=True)
    order_id       = Column(Integer, ForeignKey("orders.id"), nullable=False)
    drawing_type   = Column(String, nullable=True)
    file_reference = Column(String, nullable=True)
    status         = Column(SAEnum(DrawingStatus), default=DrawingStatus.pending)
    reviewed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at    = Column(DateTime(timezone=True), nullable=True)
    notes          = Column