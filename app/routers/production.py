from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional
from datetime import datetime, timezone, date as _date, timedelta
from zoneinfo import ZoneInfo

from app.database import get_db
from app.auth import require_user, financials_visible
from app.models.user import User, UserRole
from app.models.order import Order, OrderStatus, Priority
from app.models.production import ProductionStage, StageStatus, StageType
from app.models.work_session import WorkSession, SessionStatus, PauseReason, PAUSE_REASON_LABELS
from app.models.labor import LaborEntry, BillingDept, BILLING_RATES

_PRIORITY_RANK = {
    Priority.urgent:   0,
    Priority.priority: 1,
    Priority.standard: 2,
}

router = APIRouter(prefix="/production", tags=["production"])
templates = Jinja2Templates(directory="app/templates")

# Roles that can assign stages and change status
MANAGEMENT_ROLES = {UserRole.owner, UserRole.ops_manager, UserRole.shop_foreman}

STAGE_LABELS = {
    "material_receiving": "Material Receiving",
    "drawings":           "Drawings",
    "fabrication":        "Fabrication",
    "welding":            "Welding",
    "finishing":          "Finishing",
    "qa_qc":              "QA / Inspection",
    "delivery":           "Delivery",
}

# Default billing dept by stage type (fallback when employee has no default set)
STAGE_DEFAULT_BILLING = {
    StageType.material_receiving: BillingDept.general_labor,
    StageType.drawings:           BillingDept.general_labor,
    StageType.fabrication:        BillingDept.steel_fabrication,
    StageType.welding:            BillingDept.steel_fabrication,
    StageType.finishing:          BillingDept.general_labor,
    StageType.qa_qc:              BillingDept.general_labor,
    StageType.delivery:           BillingDept.general_labor,
}

EASTERN = ZoneInfo("America/New_York")

# Shift boundaries (Eastern)
SHIFT_START_HOUR = 7   # 7:00 AM EST
SHIFT_END_HOUR   = 16  # 4:00 PM EST


def _now_eastern() -> datetime:
    return datetime.now(tz=EASTERN)


def _shift_end_today() -> datetime:
    """Return today's 4:00 PM Eastern as a tz-aware datetime."""
    now_et = _now_eastern()
    return now_et.replace(hour=SHIFT_END_HOUR, minute=0, second=0, microsecond=0)


def _is_overtime(dt: datetime) -> bool:
    """True if the given UTC datetime falls outside 7AM–4PM Eastern."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    et = dt.astimezone(EASTERN)
    h = et.hour + et.minute / 60.0
    return h < SHIFT_START_HOUR or h >= SHIFT_END_HOUR


def _net_minutes(session: WorkSession, end: datetime) -> float:
    """Calculate net worked minutes: (end - started_at) minus total_paused_minutes."""
    total = (end - session.started_at).total_seconds() / 60.0
    return max(0.0, total - (session.total_paused_minutes or 0.0))


def auto_close_stale_sessions(db: Session):
    """
    Close any active/paused sessions from previous shifts (before today's 4 PM).
    Called at the start of board and queue page loads.
    """
    shift_end = _shift_end_today()
    now_aware = datetime.now(timezone.utc)  # keep aware for EASTERN hour check

    # If it's currently before 4 PM today, use yesterday's 4 PM as cutoff
    if now_aware.astimezone(EASTERN).hour < SHIFT_END_HOUR:
        cutoff = shift_end - timedelta(days=1)
    else:
        cutoff = shift_end

    # Strip tzinfo for DB comparison — SQLite stores naive UTC datetimes
    cutoff_utc = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    now_utc    = datetime.utcnow()

    stale = db.query(WorkSession).filter(
        WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
        WorkSession.started_at < cutoff_utc,
    ).all()

    for s in stale:
        close_at = min(cutoff_utc, now_utc)
        if s.status == SessionStatus.active:
            s.duration_minutes = _net_minutes(s, close_at)
        else:
            # Was paused — net time is what was accrued before the pause
            s.duration_minutes = _net_minutes(s, s.paused_at or close_at)
        s.ended_at    = close_at
        s.status      = SessionStatus.completed
        s.pause_reason = PauseReason.end_of_shift

        # Write LaborEntry if duration > 0
        if s.duration_minutes and s.duration_minutes > 0:
            hours = s.duration_minutes / 60.0
            entry = LaborEntry(
                order_id     = s.order_id,
                stage_id     = s.stage_id,
                employee_id  = s.employee_id,
                billing_dept = BillingDept(s.billing_dept),
                hours        = round(hours, 4),
                billing_rate = s.billing_rate,
                billed_value = round(hours * s.billing_rate, 2),
                work_date    = close_at.replace(tzinfo=timezone.utc).astimezone(EASTERN).date(),
                notes        = "Auto-closed at shift end",
                is_rework    = 0,
            )
            db.add(entry)
            db.flush()
            s.labor_entry_id = entry.id

    if stale:
        db.commit()


def _current_stage(stages: list) -> Optional[ProductionStage]:
    """Return the active stage for a job: in_progress > blocked > first pending."""
    for s in stages:
        if s.status == StageStatus.in_progress:
            return s
    for s in stages:
        if s.status == StageStatus.blocked:
            return s
    for s in stages:
        if s.status == StageStatus.pending:
            return s
    return None  # all complete


def _get_employee_billing_default(employee: User, stage: ProductionStage) -> BillingDept:
    """Employee's default dept first, then stage-type default."""
    if employee.default_billing_dept:
        return employee.default_billing_dept
    return STAGE_DEFAULT_BILLING.get(stage.stage_type, BillingDept.general_labor)


# ── Shop Floor Board ──────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
async def production_board(
    request: Request,
    filter: Optional[str] = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    auto_close_stale_sessions(db)
    today_date = _date.today()

    # Load all active orders with stages, assignments, customers
    active_orders = db.query(Order).options(
        joinedload(Order.customer),
        joinedload(Order.production_stages).joinedload(ProductionStage.assigned_to),
    ).filter(
        Order.status.notin_([OrderStatus.delivered, OrderStatus.paid, OrderStatus.cancelled])
    ).all()

    # Get active work sessions for timer indicators
    active_sessions = db.query(WorkSession).filter(
        WorkSession.status.in_([SessionStatus.active, SessionStatus.paused])
    ).all()
    active_session_by_stage = {s.stage_id: s for s in active_sessions}
    active_session_by_order = {}
    for s in active_sessions:
        active_session_by_order.setdefault(s.order_id, []).append(s)

    # Attach computed current_stage to each order for template use
    jobs = []
    for o in active_orders:
        stages = sorted(o.production_stages, key=lambda s: s.id)
        current = _current_stage(stages)
        jobs.append({
            "order":          o,
            "stages":         stages,
            "current_stage":  current,
            "has_active_timer": o.id in active_session_by_order,
        })

    # Sort: overdue first → blocked → priority flag → due date → no due date
    def _sort_key(job):
        o = job["order"]
        cs = job["current_stage"]
        is_overdue = o.promised_date and o.promised_date < today_date
        days_overdue = (today_date - o.promised_date).days if is_overdue else 0
        is_blocked = cs and cs.status == StageStatus.blocked
        priority_rank = _PRIORITY_RANK.get(o.priority, 2)
        due = o.promised_date or _date(9999, 12, 31)
        return (
            0 if is_overdue else 1,
            -days_overdue,
            0 if is_blocked else 1,
            priority_rank,
            due,
        )

    jobs.sort(key=_sort_key)

    # Apply board filter
    if filter == "unassigned":
        jobs = [j for j in jobs if j["current_stage"] and j["current_stage"].assigned_to_id is None
                and j["current_stage"].status != StageStatus.complete]
    elif filter == "blocked":
        jobs = [j for j in jobs if j["current_stage"] and j["current_stage"].status == StageStatus.blocked]
    elif filter == "in_progress":
        jobs = [j for j in jobs if j["current_stage"] and j["current_stage"].status == StageStatus.in_progress]

    # Summary counts (always from full list, not filtered)
    all_jobs = []
    for o in active_orders:
        stages = sorted(o.production_stages, key=lambda s: s.id)
        current = _current_stage(stages)
        all_jobs.append(current)

    summary = {
        "total":       len(active_orders),
        "in_progress": sum(1 for s in all_jobs if s and s.status == StageStatus.in_progress),
        "unassigned":  sum(1 for s in all_jobs if s and s.assigned_to_id is None and s.status != StageStatus.complete),
        "blocked":     sum(1 for s in all_jobs if s and s.status == StageStatus.blocked),
    }

    # Active employees for assignment dropdown
    employees = db.query(User).filter(User.is_active == True).order_by(User.first_name).all()

    can_manage = user.role in MANAGEMENT_ROLES

    return templates.TemplateResponse("production/board.html", {
        "request":      request,
        "user":         user,
        "jobs":         jobs,
        "summary":      summary,
        "employees":    employees,
        "filter":       filter,
        "stage_labels": STAGE_LABELS,
        "can_manage":   can_manage,
        "can_see_financials": financials_visible(user),
        "today":        _date.today(),
        "active_session_by_stage": active_session_by_stage,
        "now_utc":      datetime.utcnow(),
    })


# ── Assign Employee to Stage ──────────────────────────────────────────────
@router.post("/stages/{stage_id}/assign")
async def assign_stage(
    stage_id: int,
    assigned_to_id: Optional[int] = Form(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if user.role not in MANAGEMENT_ROLES:
        raise HTTPException(403, "Not authorized to assign stages")

    stage = db.query(ProductionStage).filter(ProductionStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")

    stage.assigned_to_id = assigned_to_id if assigned_to_id else None
    db.commit()
    return RedirectResponse("/production", status_code=302)


# ── Update Stage Status ───────────────────────────────────────────────────
@router.post("/stages/{stage_id}/status")
async def update_stage_status(
    stage_id: int,
    status: str = Form(...),
    notes: Optional[str] = Form(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if user.role not in MANAGEMENT_ROLES:
        raise HTTPException(403, "Not authorized to update stage status")

    stage = db.query(ProductionStage).filter(ProductionStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")

    now = datetime.utcnow()
    new_status = StageStatus(status)

    if new_status == StageStatus.in_progress and not stage.started_at:
        stage.started_at = now
    elif new_status == StageStatus.complete:
        stage.completed_at = now
        if not stage.started_at:
            stage.started_at = now
    elif new_status == StageStatus.pending:
        stage.started_at = None
        stage.completed_at = None

    stage.status = new_status
    if notes is not None:
        stage.notes = notes.strip() or None

    db.commit()
    return RedirectResponse("/production", status_code=302)


# ── Clock In ─────────────────────────────────────────────────────────────
@router.post("/stages/{stage_id}/clock-in")
async def clock_in(
    stage_id: int,
    billing_dept: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    stage = db.query(ProductionStage).options(
        joinedload(ProductionStage.assigned_to)
    ).filter(ProductionStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")

    # Employee must be assigned or be management
    if user.role not in MANAGEMENT_ROLES and stage.assigned_to_id != user.id:
        raise HTTPException(403, "You are not assigned to this stage")

    # Check for existing active session for this employee
    existing = db.query(WorkSession).filter(
        WorkSession.employee_id == user.id,
        WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
    ).first()
    if existing:
        # Auto-pause the existing session before starting new one
        now = datetime.utcnow()
        if existing.status == SessionStatus.active:
            existing.paused_at    = now
            existing.pause_reason = PauseReason.priority_shift
            existing.status       = SessionStatus.paused
        db.flush()

    now = datetime.utcnow()
    dept = BillingDept(billing_dept)
    rate = BILLING_RATES[dept]

    session = WorkSession(
        order_id     = stage.order_id,
        stage_id     = stage_id,
        employee_id  = user.id,
        billing_dept = dept.value,
        billing_rate = rate,
        started_at   = now,
        status       = SessionStatus.active,
        is_overtime  = _is_overtime(now),
    )
    db.add(session)

    # Mark stage in_progress if it's pending
    if stage.status == StageStatus.pending:
        stage.status = StageStatus.in_progress
        if not stage.started_at:
            stage.started_at = now

    db.commit()

    # Redirect back to queue page
    next_url = "/production/queue"
    return RedirectResponse(next_url, status_code=302)


# ── Pause Session ────────────────────────────────────────────────────────
@router.post("/sessions/{session_id}/pause")
async def pause_session(
    session_id: int,
    pause_reason: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    session = db.query(WorkSession).filter(WorkSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")
    if session.employee_id != user.id and user.role not in MANAGEMENT_ROLES:
        raise HTTPException(403, "Not authorized")
    if session.status != SessionStatus.active:
        raise HTTPException(400, "Session is not active")

    now = datetime.utcnow()
    session.paused_at    = now
    session.pause_reason = PauseReason(pause_reason)
    session.status       = SessionStatus.paused
    db.commit()

    return RedirectResponse("/production/queue", status_code=302)


# ── Resume Session ───────────────────────────────────────────────────────
@router.post("/sessions/{session_id}/resume")
async def resume_session(
    session_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    session = db.query(WorkSession).filter(WorkSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")
    if session.employee_id != user.id and user.role not in MANAGEMENT_ROLES:
        raise HTTPException(403, "Not authorized")
    if session.status != SessionStatus.paused:
        raise HTTPException(400, "Session is not paused")

    now = datetime.utcnow()
    # Accumulate paused time
    if session.paused_at:
        paused_minutes = (now - session.paused_at).total_seconds() / 60.0
        session.total_paused_minutes = (session.total_paused_minutes or 0.0) + paused_minutes

    session.paused_at    = None
    session.pause_reason = None
    session.status       = SessionStatus.active
    session.is_overtime  = _is_overtime(now)
    db.commit()

    return RedirectResponse("/production/queue", status_code=302)


# ── Stop Session ─────────────────────────────────────────────────────────
@router.post("/sessions/{session_id}/stop")
async def stop_session(
    session_id: int,
    notes: Optional[str] = Form(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    session = db.query(WorkSession).filter(WorkSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")
    if session.employee_id != user.id and user.role not in MANAGEMENT_ROLES:
        raise HTTPException(403, "Not authorized")
    if session.status == SessionStatus.completed:
        raise HTTPException(400, "Session already completed")

    now = datetime.utcnow()

    # If currently paused, accumulate the final paused block
    if session.status == SessionStatus.paused and session.paused_at:
        paused_minutes = (now - session.paused_at).total_seconds() / 60.0
        session.total_paused_minutes = (session.total_paused_minutes or 0.0) + paused_minutes

    duration = _net_minutes(session, now)

    session.ended_at         = now
    session.duration_minutes = duration
    session.status           = SessionStatus.completed
    if notes:
        session.notes = notes.strip() or None

    # Write LaborEntry
    if duration > 0:
        hours = duration / 60.0
        work_date = now.replace(tzinfo=timezone.utc).astimezone(EASTERN).date()
        dept  = BillingDept(session.billing_dept)
        entry = LaborEntry(
            order_id     = session.order_id,
            stage_id     = session.stage_id,
            employee_id  = session.employee_id,
            billing_dept = dept,
            hours        = round(hours, 4),
            billing_rate = session.billing_rate,
            billed_value = round(hours * session.billing_rate, 2),
            work_date    = work_date,
            notes        = session.notes,
            is_rework    = 0,
        )
        db.add(entry)
        db.flush()
        session.labor_entry_id = entry.id

    db.commit()
    return RedirectResponse("/production/queue", status_code=302)


# ── Employee Queue ───────────────────────────────────────────────────────
@router.get("/queue", response_class=HTMLResponse)
async def employee_queue(
    request: Request,
    emp_id: Optional[int] = None,   # management can view any employee's queue
    stat_period: str = "day",        # day | week | month
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    auto_close_stale_sessions(db)
    can_manage = user.role in MANAGEMENT_ROLES
    today_date = _date.today()

    # Determine whose queue to show
    if can_manage and emp_id:
        view_employee = db.query(User).filter(User.id == emp_id).first()
        if not view_employee:
            raise HTTPException(404, "Employee not found")
    else:
        view_employee = user

    # Active session for this employee
    my_session = db.query(WorkSession).filter(
        WorkSession.employee_id == view_employee.id,
        WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
    ).options(
        joinedload(WorkSession.stage),
        joinedload(WorkSession.order).joinedload(Order.customer),
    ).first()

    # All active orders assigned to this employee at any stage
    from app.models.order import OrderLineItem
    my_stages = db.query(ProductionStage).options(
        joinedload(ProductionStage.order).joinedload(Order.customer),
        joinedload(ProductionStage.order).joinedload(Order.line_items),
    ).filter(
        ProductionStage.assigned_to_id == view_employee.id,
        ProductionStage.status.notin_([StageStatus.complete]),
    ).all()

    # Build job cards, sorted by priority
    jobs_by_order = {}
    for stage in my_stages:
        o = stage.order
        if o.status in [OrderStatus.delivered, OrderStatus.paid, OrderStatus.cancelled]:
            continue
        if o.id not in jobs_by_order:
            jobs_by_order[o.id] = {"order": o, "stages": []}
        jobs_by_order[o.id]["stages"].append(stage)

    job_list = list(jobs_by_order.values())

    def _sort_key(job):
        o = job["order"]
        is_overdue = o.promised_date and o.promised_date < today_date
        days_overdue = (today_date - o.promised_date).days if is_overdue else 0
        priority_rank = _PRIORITY_RANK.get(o.priority, 2)
        due = o.promised_date or _date(9999, 12, 31)
        return (0 if is_overdue else 1, -days_overdue, priority_rank, due)

    job_list.sort(key=_sort_key)

    # Build billing options for clock-in form
    billing_options = [
        (BillingDept.general_labor,       "General Labor ($80/hr)"),
        (BillingDept.steel_fabrication,   "Steel Fabrication ($100/hr)"),
        (BillingDept.aluminum_structural, "Aluminum Structural ($120/hr)"),
    ]

    # Management: also load all employee queues summary
    all_employees_summary = []
    if can_manage:
        active_employees = db.query(User).filter(User.is_active == True).order_by(User.first_name).all()
        for emp in active_employees:
            emp_session = db.query(WorkSession).filter(
                WorkSession.employee_id == emp.id,
                WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
            ).first()
            emp_stage_count = db.query(ProductionStage).filter(
                ProductionStage.assigned_to_id == emp.id,
                ProductionStage.status.notin_([StageStatus.complete]),
            ).count()
            all_employees_summary.append({
                "employee":    emp,
                "session":     emp_session,
                "stage_count": emp_stage_count,
            })

    now_utc = datetime.utcnow()

    # ── My Stats ─────────────────────────────────────────────────────────
    stat_period = stat_period if stat_period in ("day", "week", "month") else "day"
    today = today_date
    if stat_period == "day":
        period_start = today
    elif stat_period == "week":
        period_start = today - timedelta(days=today.weekday())  # Monday
    else:  # month
        period_start = today.replace(day=1)

    completed_sessions = db.query(WorkSession).filter(
        WorkSession.employee_id == view_employee.id,
        WorkSession.status