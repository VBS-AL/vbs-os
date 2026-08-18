from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, Enum as SAEnum, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class EquipmentType(str, enum.Enum):
    truck        = "truck"
    forklift     = "forklift"
    paint_sprayer = "paint_sprayer"
    welding_equip = "welding_equip"
    saw          = "saw"
    bathroom     = "bathroom"
    trash        = "trash"
    other        = "other"


class FrequencyType(str, enum.Enum):
    daily_weekday = "daily_weekday"   # M–F each day
    weekly        = "weekly"          # once per week
    biweekly      = "biweekly"        # every 2 weeks
    monthly       = "monthly"         # once per month
    every_6mo     = "every_6mo"       # twice a year
    mileage       = "mileage"         # triggered by mileage threshold


EQUIPMENT_LABELS = {
    EquipmentType.truck:         "Truck",
    EquipmentType.forklift:      "Forklift / Hi-Lo",
    EquipmentType.paint_sprayer: "Paint Sprayer",
    EquipmentType.welding_equip: "Welding Equipment",
    EquipmentType.saw:           "Saw",
    EquipmentType.bathroom:      "Bathroom",
    EquipmentType.trash:         "Trash",
    EquipmentType.other:         "Other",
}

FREQ_LABELS = {
    FrequencyType.daily_weekday: "Daily (M–F)",
    FrequencyType.weekly:        "Weekly",
    FrequencyType.biweekly:      "Every 2 Weeks",
    FrequencyType.monthly:       "Monthly",
    FrequencyType.every_6mo:     "Every 6 Months",
    FrequencyType.mileage:       "Mileage-Based",
}


class MaintenanceTask(Base):
    """Defines a repeating PM task for a piece of equipment."""
    __tablename__ = "maintenance_tasks"

    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String, nullable=False)           # e.g. "Oil Change — Truck 1"
    equipment_type  = Column(SAEnum(EquipmentType), nullable=False)
    equipment_label = Column(String, nullable=True)            # "Truck 1", "Hi-Lo 2", etc.
    frequency       = Column(SAEnum(FrequencyType), nullable=False)
    # Mileage-based fields (trucks)
    mileage_interval = Column(Float, nullable=True)            # e.g. 5000 miles
    current_mileage  = Column(Float, nullable=True, default=0)
    last_mileage     = Column(Float, nullable=True, default=0) # mileage at last completion
    # Next due tracking
    next_due_date   = Column(Date, nullable=True)
    # Assignees: list of user IDs (JSON, stored as TEXT in SQLite)
    assigned_user_ids = Column(JSON, nullable=True, default=list)
    # Flags
    is_active       = Column(Boolean, default=True)
    notes           = Column(Text, nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    logs = relationship("MaintenanceLog", back_populates="task",
                        order_by="MaintenanceLog.completed_at.desc()",
                        cascade="all, delete-orphan")


class MaintenanceLog(Base):
    """Records a single completion of a PM task."""
    __tablename__ = "maintenance_logs"

    id              = Column(Integer, primary_key=True, index=True)
    task_id         = Column(Integer, ForeignKey("maintenance_tasks.id"), nullable=False)
    completed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    completed_at    = Column(DateTime(timezone=True), server_default=func.now())
    mileage_at_log  = Column(Float, nullable=True)             # snapshot for mileage tasks
    notes           = Column(Text, nullable=True)
    overridden_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # admin/ops override

    task         = relationship("MaintenanceTask", back_populates="logs")
    completed_by = relationship("User", foreign_keys=[completed_by_id])
    overridden_by = relationship("User", foreign_keys=[overridden_by_id])


class PMRequestStatus(str, enum.Enum):
    pending  = "pending"
    approved = "approved"
    dismissed = "dismissed"


class MaintenanceRequest(Base):
    """A PM task request submitted by any team member for management review."""
    __tablename__ = "maintenance_requests"

    id               = Column(Integer, primary_key=True, index=True)
    requested_by_id  = Column(Integer, ForeignKey("users.id"), nullable=False)
    equipment_type   = Column(SAEnum(EquipmentType), nullable=True)
    equipment_label  = Column(String, nullable=True)   # "Truck 1", "Hi-Lo 2", etc.
    description      = Column(Text, nullable=False)    # what needs doing
    notes            = Column(Text, nullable=True)
    status           = Column(SAEnum(PMRequestStatus), default=PMRequestStatus.pending)
    reviewed_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at      = Column(DateTime(timezone=True), nullable=True)
    review_note      = Column(Text, nullable=True)     # reason if dismissed
    task_id          = Column(Integer, ForeignKey("maintenance_tasks.id"), nullable=True)  # set on approval
    requested_at     = Column(DateTime(timezone=True), server_default=func.now())

    requested_by = relationship("User", foreign_keys=[requested_by_id])
    reviewed_by  = relationship("User", foreign_keys=[reviewed_by_id])
    task         = relationship("MaintenanceTask", foreign_keys=[task_id])


class MileageLog(Base):
    """Weekly mileage check-in for trucks (logged every Monday)."""
    __tablename__ = "mileage_logs"

    id          = Column(Integer, primary_key=True, index=True)
    task_id     = Column(Integer, ForeignKey("maintenance_tasks.id"), nullable=False)
    logged_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    log_date    = Column(Date, nullable=False)
    mileage     = Column(Float, nullable=False)
    notes       = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    task      = relationship("MaintenanceTask")
    logged_by = relationship("User", foreign_keys=[logged_by_id])
