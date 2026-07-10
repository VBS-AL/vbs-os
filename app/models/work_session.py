from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum, Text, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class SessionStatus(str, enum.Enum):
    active    = "active"
    paused    = "paused"
    completed = "completed"


class PauseReason(str, enum.Enum):
    waiting_material  = "waiting_material"
    break_lunch       = "break_lunch"
    priority_shift    = "priority_shift"
    equipment_issue   = "equipment_issue"
    waiting_drawings  = "waiting_drawings"
    end_of_shift      = "end_of_shift"


PAUSE_REASON_LABELS = {
    PauseReason.waiting_material: "Waiting on Material",
    PauseReason.break_lunch:      "Break / Lunch",
    PauseReason.priority_shift:   "Moved to Priority Job",
    PauseReason.equipment_issue:  "Machine / Equipment Issue",
    PauseReason.waiting_drawings: "Waiting on Drawings / Approval",
    PauseReason.end_of_shift:     "End of Shift",
}


class WorkSession(Base):
    __tablename__ = "work_sessions"

    id                  = Column(Integer, primary_key=True, index=True)
    order_id            = Column(Integer, ForeignKey("orders.id"), nullable=False)
    stage_id            = Column(Integer, ForeignKey("production_stages.id"), nullable=False)
    employee_id         = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Billing
    billing_dept        = Column(String, nullable=False)   # matches BillingDept enum values
    billing_rate        = Column(Float, nullable=False)    # snapshot at session start

    # Timing
    started_at          = Column(DateTime(timezone=True), nullable=False)
    paused_at           = Column(DateTime(timezone=True), nullable=True)   # set when paused
    ended_at            = Column(DateTime(timezone=True), nullable=True)   # set when stopped
    total_paused_minutes = Column(Float, default=0.0)       # cumulative paused time

    # State
    status              = Column(SAEnum(SessionStatus), default=SessionStatus.active)
    pause_reason        = Column(SAEnum(PauseReason), nullable=True)  # last pause reason
    is_overtime         = Column(Boolean, default=False)

    # Outcome (filled when completed)
    duration_minutes    = Column(Float, nullable=True)   # net worked time
    labor_entry_id      = Column(Integer, ForeignKey("labor_entries.id"), nullable=True)

    notes               = Column(Text, nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    order               = relationship("Order",           back_populates="work_sessions")
    stage               = relationship("ProductionStage", back_populates="work_sessions")
    employee            = relationship("User",            back_populates="work_sessions")
    labor_entry         = relationship("LaborEntry",      foreign_keys=[labor_entry_id])
