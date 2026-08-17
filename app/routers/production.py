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
from app.models.production import ProductionStage, StageStatus, StageType, QARecord, QAResult, DrawingRecord
from app.models.packing_list import PackingList, ShippedVia
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
    "rework":             "Rework",
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
    StageType.rework:             BillingDept.steel_fabrication,
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

    # Load all active orders with stages, assignments, customers, and work sessions
    active_orders = db.query(Order).options(
        joinedload(Order.customer),
        joinedload(Order.production_stages).joinedload(ProductionStage.assigned_to),
        joinedload(Order.production_stages).joinedload(ProductionStage.work_sessions),
        joinedload(Order.drawing_records).joinedload(DrawingRecord.uploaded_by),
    ).filter(
        Order.status.notin_([OrderStatus.delivered, OrderStatus.invoiced, OrderStatus.paid, OrderStatus.cancelled])
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
    redirect_to: str = Form("/production"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    stage = db.query(ProductionStage).filter(ProductionStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")

    # Management can do anything; workers can only complete/block their own assigned stage
    is_management = user.role in MANAGEMENT_ROLES
    if not is_management:
        if stage.assigned_to_id != user.id:
            raise HTTPException(403, "Not authorized to update this stage")
        if StageStatus(status) not in (StageStatus.complete, StageStatus.blocked, StageStatus.in_progress):
            raise HTTPException(403, "Not authorized to update stage status")

    now = datetime.utcnow()
    new_status = StageStatus(status)

    # Gate: require at least one completed work session before marking any stage done
    if new_status == StageStatus.complete:
        has_time = db.query(WorkSession).filter(
            WorkSession.stage_id == stage_id,
            WorkSession.status == SessionStatus.completed,
        ).first()
        if not has_time:
            raise HTTPException(400, "Log time on this stage before marking it complete")

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

    # Auto-advance: when a stage completes, kick the next pending stage to in_progress
    if new_status == StageStatus.complete:
        order_stages = db.query(ProductionStage).filter(
            ProductionStage.order_id == stage.order_id
        ).order_by(ProductionStage.id).all()
        found = False
        advanced = False
        for s in order_stages:
            if found and s.status == StageStatus.pending:
                s.status = StageStatus.in_progress
                s.started_at = now
                advanced = True
                break
            if s.id == stage.id:
                found = True
        # Special case: completing a rework stage → re-advance the pending QA stage
        if not advanced and stage.stage_type == StageType.rework:
            qa_stage = db.query(ProductionStage).filter(
                ProductionStage.order_id == stage.order_id,
                ProductionStage.stage_type == StageType.qa_qc,
                ProductionStage.status == StageStatus.pending,
            ).first()
            if qa_stage:
                qa_stage.status = StageStatus.in_progress
                qa_stage.started_at = now

        # Completing a delivery stage: stop the running timer + mark order delivered
        if stage.stage_type == StageType.delivery:
            order = db.query(Order).filter(Order.id == stage.order_id).first()
            if order and order.status == OrderStatus.ready:
                order.status = OrderStatus.delivered
            # Stop any still-running session for this stage
            open_session = db.query(WorkSession).filter(
                WorkSession.stage_id == stage_id,
                WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
            ).first()
            if open_session:
                if open_session.status == SessionStatus.paused and open_session.paused_at:
                    open_session.total_paused_minutes = (open_session.total_paused_minutes or 0.0) + \
                        (now - open_session.paused_at).total_seconds() / 60.0
                duration = _net_minutes(open_session, now)
                open_session.ended_at         = now
                open_session.duration_minutes = duration
                open_session.status           = SessionStatus.completed
                if duration > 0:
                    work_date = now.replace(tzinfo=timezone.utc).astimezone(EASTERN).date()
                    dept      = BillingDept(open_session.billing_dept)
                    entry = LaborEntry(
                        order_id     = open_session.order_id,
                        stage_id     = open_session.stage_id,
                        employee_id  = open_session.employee_id,
                        billing_dept = dept,
                        hours        = round(duration / 60.0, 4),
                        billing_rate = open_session.billing_rate,
                        billed_value = 0.0,  # delivery is non-billable
                        work_date    = work_date,
                        notes        = open_session.notes,
                        is_rework    = 0,
                    )
                    db.add(entry)
                    db.flush()
                    open_session.labor_entry_id = entry.id

    db.commit()
    return RedirectResponse(redirect_to, status_code=302)


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
    # Delivery time is tracked for internal purposes only — no billable charge
    rate = 0.0 if stage.stage_type == StageType.delivery else BILLING_RATES[dept]

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


# ── QA Complete (stop session + record result + complete stage) ───────────
@router.post("/stages/{stage_id}/qa-complete")
async def qa_complete(
    request: Request,
    stage_id: int,
    result: str = Form(...),
    failure_reason: Optional[str] = Form(None),
    rework_notes: Optional[str] = Form(None),
    certified_weld: bool = Form(False),
    cert_reference: Optional[str] = Form(None),
    session_notes: Optional[str] = Form(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    from app.models.order import OrderLineItem
    from app.models.inventory import InventoryItem as _InvItem
    stage = db.query(ProductionStage).options(
        joinedload(ProductionStage.order).joinedload(Order.line_items).joinedload(OrderLineItem.inventory_item)
    ).filter(ProductionStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")
    if stage.stage_type != StageType.qa_qc:
        raise HTTPException(400, "Stage is not a QA stage")
    if stage.assigned_to_id != user.id and user.role not in MANAGEMENT_ROLES:
        raise HTTPException(403, "Not authorized")

    now = datetime.utcnow()

    # Stop the active work session for this stage
    active_session = db.query(WorkSession).filter(
        WorkSession.stage_id == stage_id,
        WorkSession.employee_id == user.id,
        WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
    ).first()

    if active_session:
        if active_session.status == SessionStatus.paused and active_session.paused_at:
            paused_minutes = (now - active_session.paused_at).total_seconds() / 60.0
            active_session.total_paused_minutes = (active_session.total_paused_minutes or 0.0) + paused_minutes

        duration = _net_minutes(active_session, now)
        active_session.ended_at         = now
        active_session.duration_minutes = duration
        active_session.status           = SessionStatus.completed
        if session_notes:
            active_session.notes = session_notes.strip() or None

        if duration > 0:
            hours     = duration / 60.0
            work_date = now.replace(tzinfo=timezone.utc).astimezone(EASTERN).date()
            dept      = BillingDept(active_session.billing_dept)
            entry = LaborEntry(
                order_id     = active_session.order_id,
                stage_id     = active_session.stage_id,
                employee_id  = active_session.employee_id,
                billing_dept = dept,
                hours        = round(hours, 4),
                billing_rate = active_session.billing_rate,
                billed_value = round(hours * active_session.billing_rate, 2),
                work_date    = work_date,
                notes        = active_session.notes,
                is_rework    = 0,
            )
            db.add(entry)
            db.flush()
            active_session.labor_entry_id = entry.id

    # Create QA record
    qa = QARecord(
        order_id       = stage.order_id,
        inspector_id   = user.id,
        result         = QAResult(result),
        failure_reason = failure_reason.strip() if failure_reason else None,
        rework_notes   = rework_notes.strip() if rework_notes else None,
        certified_weld = certified_weld,
        cert_reference = cert_reference.strip() if cert_reference else None,
    )
    db.add(qa)

    # Mark stage complete
    stage.status       = StageStatus.complete
    stage.completed_at = now
    if not stage.started_at:
        stage.started_at = now

    # Auto-advance next pending stage
    order_stages = db.query(ProductionStage).filter(
        ProductionStage.order_id == stage.order_id
    ).order_by(ProductionStage.id).all()
    found = False
    for s in order_stages:
        if found and s.status == StageStatus.pending:
            s.status     = StageStatus.in_progress
            s.started_at = now
            break
        if s.id == stage.id:
            found = True

    # Advance order status based on QA result
    order = db.query(Order).filter(Order.id == stage.order_id).first()
    if order:
        qa_result = QAResult(result)
        if qa_result in (QAResult.pass_result, QAResult.conditional):
            # Pass or Conditional → ready for delivery
            order.status = OrderStatus.ready
        elif qa_result == QAResult.rework:
            # Rework → send back to production
            order.status = OrderStatus.in_production
            order.rework_count = (order.rework_count or 0) + 1
            # Reset the QA stage so it can be redone after rework
            stage.status       = StageStatus.pending
            stage.started_at   = None
            stage.completed_at = None
            # Reset delivery stage back to pending so it leaves the work queue
            for s in order_stages:
                if s.stage_type == StageType.delivery and s.id != stage.id:
                    s.status       = StageStatus.pending
                    s.started_at   = None
                    s.completed_at = None
            # Insert a Rework stage assigned to the QA inspector so it shows in the queue
            db.flush()
            rework_stage = ProductionStage(
                order_id      = stage.order_id,
                stage_type    = StageType.rework,
                status        = StageStatus.in_progress,
                assigned_to_id= stage.assigned_to_id,
                started_at    = now,
            )
            db.add(rework_stage)
        else:
            # Fail → send to QA review for foreman decision
            order.status = OrderStatus.qa_review

    # ── Process remnant records ───────────────────────────────────────────
    from app.models.scrap import RemnantRecord, RemnantDisposition
    from app.models.inventory import InventoryItem, InventoryAdjustment, AdjustmentReason
    from app.models.order import OrderLineItem
    form_data = await request.form()
    if order:
        for li in order.line_items:
            qty_key  = f"remnant_qty_{li.id}"
            disp_key = f"remnant_disp_{li.id}"
            qty_val  = form_data.get(qty_key, "")
            disp_val = form_data.get(disp_key, "")
            if not qty_val or not disp_val:
                continue
            try:
                qty_remaining = float(qty_val)
            except ValueError:
                continue
            if qty_remaining <= 0:
                continue
            unit_cost = (li.unit_price or 0) * qty_remaining
            remnant = RemnantRecord(
                order_id          = order.id,
                line_item_id      = li.id,
                inventory_item_id = li.inventory_item_id,
                description       = li.description,
                qty_remaining     = qty_remaining,
                unit              = li.inventory_item.unit if li.inventory_item else None,
                unit_cost         = unit_cost,
                disposition       = RemnantDisposition(disp_val),
                logged_by_id      = user.id,
            )
            db.add(remnant)
            if disp_val == RemnantDisposition.back_to_stock and li.inventory_item_id:
                inv_item = db.query(InventoryItem).filter(InventoryItem.id == li.inventory_item_id).first()
                if inv_item:
                    inv_item.quantity_on_hand = (inv_item.quantity_on_hand or 0) + qty_remaining
                    adj = InventoryAdjustment(
                        item_id        = inv_item.id,
                        recorded_by_id = user.id,
                        reason         = AdjustmentReason.return_to_stock,
                        delta          = qty_remaining,
                        order_id       = order.id,
                        notes          = f"Remnant from {order.order_number}",
                    )
                    db.add(adj)
            elif disp_val == RemnantDisposition.retail:
                from app.models.scrap import RetailScrapItem, DisplayStatus
                from datetime import date as _date_type
                retail_item = RetailScrapItem(
                    description   = f"{li.description} — remnant ({qty_remaining} {li.inventory_item.unit if li.inventory_item else 'pcs'})",
                    material_type = li.inventory_item.category.value if li.inventory_item else "Unknown",
                    retail_price  = unit_cost,
                    status        = DisplayStatus.available,
                    date_added    = _date_type.today(),
                    notes         = f"From order {order.order_number}",
                )
                db.add(retail_item)

    db.commit()
    return RedirectResponse("/production/queue", status_code=302)


# ── Delivery Complete (stop session + save packing list + complete stage) ─
@router.post("/stages/{stage_id}/delivery-complete")
async def delivery_complete(
    stage_id: int,
    shipped_via: str = Form(...),
    shipped_via_other: Optional[str] = Form(None),
    date_shipped: Optional[str] = Form(None),
    ship_to: Optional[str] = Form(None),
    sold_to: Optional[str] = Form(None),
    contact_name: Optional[str] = Form(None),
    contact_phone: Optional[str] = Form(None),
    cartons: Optional[int] = Form(None),
    total_weight: Optional[float] = Form(None),
    order_complete: bool = Form(False),
    balance_to_follow: bool = Form(False),
    packed_by: Optional[str] = Form(None),
    checker_id: Optional[int] = Form(None),
    notes: Optional[str] = Form(None),
    session_notes: Optional[str] = Form(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    from datetime import date as _date_type
    stage = db.query(ProductionStage).options(
        joinedload(ProductionStage.order).joinedload(Order.line_items)
    ).filter(ProductionStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")
    if stage.stage_type != StageType.delivery:
        raise HTTPException(400, "Stage is not a delivery stage")
    if stage.assigned_to_id != user.id and user.role not in MANAGEMENT_ROLES:
        raise HTTPException(403, "Not authorized")

    now = datetime.utcnow()

    # Do NOT stop the session here — the timer keeps running until delivery is confirmed.
    # That way we track total time from packing start to confirmed delivery.
    # Store notes on the session for now if provided.
    active_session = db.query(WorkSession).filter(
        WorkSession.stage_id == stage_id,
        WorkSession.employee_id == user.id,
        WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
    ).first()
    if active_session and session_notes:
        active_session.notes = session_notes.strip() or None

    # Auto-calculate weight from inventory line items if not provided
    order = db.query(Order).options(joinedload(Order.line_items)).filter(Order.id == stage.order_id).first()
    if total_weight is None and order:
        calc_weight = 0.0
        for li in order.line_items:
            if li.inventory_item and li.inventory_item.weight_per_unit:
                calc_weight += li.quantity * li.inventory_item.weight_per_unit
        total_weight = round(calc_weight, 2) if calc_weight > 0 else None

    # Parse date
    shipped_date = None
    if date_shipped:
        try:
            shipped_date = _date_type.fromisoformat(date_shipped)
        except ValueError:
            shipped_date = None
    if not shipped_date:
        shipped_date = now.replace(tzinfo=timezone.utc).astimezone(EASTERN).date()

    # Create or update packing list
    from datetime import date as _d
    pl = db.query(PackingList).filter(PackingList.order_id == stage.order_id).first()
    if not pl:
        # Generate sequential PL number: VBS-PL-YY-XXXXX
        yy      = str(_d.today().year)[2:]
        pattern = f"VBS-PL-{yy}-"
        last    = db.query(PackingList.pl_number).filter(
            PackingList.pl_number.like(f"{pattern}%")
        ).order_by(PackingList.pl_number.desc()).first()
        next_n  = (int(last[0].split("-")[-1]) + 1) if (last and last[0]) else 1
        # Pre-populate shipped_via from order's preferred delivery method
        _order = db.query(Order).filter(Order.id == stage.order_id).first()
        _pre_shipped_via = None
        if _order and _order.preferred_delivery_method == "customer_pickup":
            _pre_shipped_via = ShippedVia.customer_pickup
        elif _order and _order.preferred_delivery_method == "delivery":
            _pre_shipped_via = ShippedVia.vbs_delivery
        pl = PackingList(
            order_id      = stage.order_id,
            created_by_id = user.id,
            pl_number     = f"{pattern}{next_n:05d}",
            shipped_via   = _pre_shipped_via,
        )
        db.add(pl)

    pl.shipped_via       = ShippedVia(shipped_via)
    pl.shipped_via_other = shipped_via_other.strip() if shipped_via_other else None
    pl.date_shipped      = shipped_date
    pl.ship_to           = ship_to.strip() if ship_to else None
    pl.sold_to           = sold_to.strip() if sold_to else None
    pl.contact_name      = contact_name.strip() if contact_name else None
    pl.contact_phone     = contact_phone.strip() if contact_phone else None
    pl.cartons           = cartons
    pl.total_weight      = total_weight
    pl.order_complete    = order_complete
    pl.balance_to_follow = balance_to_follow
    pl.packed_by         = packed_by.strip() if packed_by else None
    # Resolve checker by ID so the name is authoritative and we can enforce confirmation
    checker_user = db.query(User).filter(User.id == checker_id).first() if checker_id else None
    pl.checker_id        = checker_id if checker_user else None
    pl.checked_by        = f"{checker_user.first_name} {checker_user.last_name or ''}".strip() if checker_user else None
    pl.check_confirmed   = False   # checker must confirm in their own queue
    pl.notes             = notes.strip() if notes else None

    # Leave delivery stage in_progress — it completes only when delivery is confirmed.
    # "Mark as Delivered" on the packing list view (or "✓ Done" in the queue) finalises it.

    db.commit()
    return RedirectResponse(f"/orders/{stage.order_id}/packing-list", status_code=302)


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
    from app.models.order import OrderLineItem
    from app.models.inventory import InventoryItem
    my_session = db.query(WorkSession).filter(
        WorkSession.employee_id == view_employee.id,
        WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
    ).options(
        joinedload(WorkSession.stage),
        joinedload(WorkSession.order).joinedload(Order.customer),
        joinedload(WorkSession.order).joinedload(Order.line_items).joinedload(OrderLineItem.inventory_item),
        joinedload(WorkSession.order).joinedload(Order.packing_list),
        joinedload(WorkSession.order).joinedload(Order.drawing_records).joinedload(DrawingRecord.uploaded_by),
    ).first()

    # All active orders assigned to this employee at any stage
    from app.models.order import OrderLineItem
    my_stages = db.query(ProductionStage).options(
        joinedload(ProductionStage.order).joinedload(Order.customer),
        joinedload(ProductionStage.order).joinedload(Order.line_items),
        joinedload(ProductionStage.order).joinedload(Order.drawing_records).joinedload(DrawingRecord.uploaded_by),
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
        (BillingDept.hot_walk_in,         "HOT Walk-In ($150/hr)"),
        (BillingDept.welding_truck,       "Welding Truck ($120/hr)"),
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
        WorkSession.status == SessionStatus.completed,
    ).join(WorkSession.labor_entry).filter(
        LaborEntry.work_date >= period_start,
    ).options(joinedload(WorkSession.labor_entry)).all()

    stat_hours  = round(sum((s.labor_entry.hours or 0) for s in completed_sessions if s.labor_entry), 1)
    stat_orders = len(set(s.order_id for s in completed_sessions))

    default_billing = _get_employee_billing_default(view_employee, my_stages[0]) if my_stages else None

    # All active employees — for Checked By dropdown on delivery form
    all_employees = db.query(User).filter(User.is_active == True).order_by(User.first_name).all()

    # Packing lists awaiting confirmation by this employee (checker role)
    from app.models.packing_list import PackingList
    pending_checks = db.query(PackingList).options(
        joinedload(PackingList.order).joinedload(Order.customer),
        joinedload(PackingList.created_by),
    ).filter(
        PackingList.checker_id == view_employee.id,
        PackingList.check_confirmed == False,
    ).all()

    return templates.TemplateResponse("production/queue.html", {
        "request":               request,
        "user":                  user,
        "view_employee":         view_employee,
        "my_session":            my_session,
        "job_list":              job_list,
        "today":                 today_date,
        "now_utc":               now_utc,
        "billing_options":       billing_options,
        "default_billing":       default_billing,
        "pause_reasons":         PAUSE_REASON_LABELS,
        "stage_labels":          STAGE_LABELS,
        "can_manage":            can_manage,
        "all_employees_summary": all_employees_summary,
        "all_employees":         all_employees,
        "pending_checks":        pending_checks,
        "stat_hours":            stat_hours,
        "stat_orders":           stat_orders,
        "stat_period":           stat_period,
        "emp_id":                emp_id,
    })
