from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, timedelta
from typing import Optional

from app.database import get_db
from app.auth import get_current_user, financials_visible
from app.models.maintenance import (
    MaintenanceTask, MaintenanceLog, MileageLog,
    MaintenanceRequest, PMRequestStatus,
    EquipmentType, FrequencyType, EQUIPMENT_LABELS, FREQ_LABELS
)
from app.models.user import User as UserModel

router = APIRouter(prefix="/maintenance", tags=["maintenance"])
templates = Jinja2Templates(directory="app/templates")


def _compute_next_due(task: MaintenanceTask, from_date: date) -> Optional[date]:
    """Calculate next_due_date based on frequency."""
    f = task.frequency
    if f == FrequencyType.daily_weekday:
        d = from_date + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d
    elif f == FrequencyType.weekly:
        return from_date + timedelta(weeks=1)
    elif f == FrequencyType.biweekly:
        return from_date + timedelta(weeks=2)
    elif f == FrequencyType.monthly:
        m = from_date.month + 1
        y = from_date.year + (m > 12)
        m = m if m <= 12 else m - 12
        return from_date.replace(year=y, month=m)
    elif f == FrequencyType.every_6mo:
        m = from_date.month + 6
        y = from_date.year + (m > 12)
        m = m if m <= 12 else m - 12
        return from_date.replace(year=y, month=m)
    return None  # mileage-based: no date trigger


# ── List ──────────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
async def maintenance_list(
    request: Request,
    flash: Optional[str] = None,
    mine: Optional[str]  = None,   # "1" = show only my assigned tasks
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)

    today = date.today()
    tasks = db.query(MaintenanceTask).filter(
        MaintenanceTask.is_active == True
    ).order_by(MaintenanceTask.equipment_type, MaintenanceTask.name).all()

    # Load last completion log per task
    last_log_map = {}
    for t in tasks:
        last = db.query(MaintenanceLog).filter(
            MaintenanceLog.task_id == t.id
        ).order_by(MaintenanceLog.completed_at.desc()).first()
        last_log_map[t.id] = last

    # Tag each task: overdue / due_today / due_soon / ok
    for t in tasks:
        t._last_log = last_log_map.get(t.id)
        if t.frequency == FrequencyType.mileage:
            miles_since = (t.current_mileage or 0) - (t.last_mileage or 0)
            t._status = "overdue" if miles_since >= (t.mileage_interval or 9999) else "ok"
            t._miles_since = miles_since
        else:
            if t.next_due_date is None:
                t._status = "ok"
            elif t.next_due_date < today:
                t._status = "overdue"
            elif t.next_due_date == today:
                t._status = "due_today"
            elif t.next_due_date <= today + timedelta(days=3):
                t._status = "due_soon"
            else:
                t._status = "ok"

    # Filter to "mine" if requested
    if mine == "1":
        tasks = [t for t in tasks if t.assigned_user_ids and user.id in (t.assigned_user_ids or [])]

    overdue   = [t for t in tasks if t._status == "overdue"]
    due_today = [t for t in tasks if t._status == "due_today"]
    due_soon  = [t for t in tasks if t._status == "due_soon"]
    ok        = [t for t in tasks if t._status == "ok"]

    # Build user map for displaying assignee names
    all_users = db.query(UserModel).filter(UserModel.is_active == True).order_by(UserModel.first_name).all()
    user_map  = {u.id: u for u in all_users}

    pending_requests = db.query(MaintenanceRequest).filter(
        MaintenanceRequest.status == PMRequestStatus.pending
    ).count()

    return templates.TemplateResponse("maintenance/list.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
        "tasks": tasks,
        "overdue": overdue, "due_today": due_today,
        "due_soon": due_soon, "ok": ok,
        "today": today,
        "flash": flash,
        "mine": mine,
        "user_map": user_map,
        "pending_requests": pending_requests,
        "equipment_labels": EQUIPMENT_LABELS,
        "freq_labels": FREQ_LABELS,
        "equipment_types": EquipmentType,
        "freq_types": FrequencyType,
    })


# ── New task form ─────────────────────────────────────────────────────────────
@router.get("/new", response_class=HTMLResponse)
async def new_task_form(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    all_users = db.query(UserModel).filter(UserModel.is_active == True).order_by(UserModel.first_name).all()
    return templates.TemplateResponse("maintenance/new.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
        "equipment_types": EquipmentType,
        "freq_types": FrequencyType,
        "equipment_labels": EQUIPMENT_LABELS,
        "freq_labels": FREQ_LABELS,
        "all_users": all_users,
    })


@router.post("/new")
async def create_task(
    request: Request,
    name: str              = Form(...),
    equipment_type: str    = Form(...),
    equipment_label: str   = Form(""),
    frequency: str         = Form(...),
    mileage_interval: Optional[float] = Form(None),
    next_due_date: Optional[str]      = Form(None),
    notes: str             = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    form_data = await request.form()
    assignee_ids = [int(v) for v in form_data.getlist("assignee_ids") if v]
    due = date.fromisoformat(next_due_date) if next_due_date else None
    task = MaintenanceTask(
        name=name,
        equipment_type=EquipmentType(equipment_type),
        equipment_label=equipment_label or None,
        frequency=FrequencyType(frequency),
        mileage_interval=mileage_interval,
        next_due_date=due,
        notes=notes or None,
        assigned_user_ids=assignee_ids or None,
    )
    db.add(task)
    db.commit()
    return RedirectResponse("/maintenance", status_code=302)


# ── Mark complete ─────────────────────────────────────────────────────────────
@router.post("/{task_id}/complete")
async def complete_task(
    task_id: int,
    mileage_at_log: Optional[float] = Form(None),
    notes: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    task = db.get(MaintenanceTask, task_id)
    if not task:
        return RedirectResponse("/maintenance", status_code=302)

    today = date.today()
    log = MaintenanceLog(
        task_id=task_id,
        completed_by_id=user.id,
        mileage_at_log=mileage_at_log,
        notes=notes or None,
    )
    db.add(log)

    # Update task tracking
    if task.frequency == FrequencyType.mileage and mileage_at_log:
        task.last_mileage = mileage_at_log
        task.current_mileage = mileage_at_log
        flash_msg = f"{task.name} logged at {int(mileage_at_log)} mi"
    else:
        next_due = _compute_next_due(task, today)
        task.next_due_date = next_due
        if next_due:
            flash_msg = f"{task.name} — done ✓  Next due {next_due.strftime('%b %d')}"
        else:
            flash_msg = f"{task.name} marked complete"

    db.commit()
    from urllib.parse import quote as url_quote
    return RedirectResponse(f"/maintenance?flash={url_quote(flash_msg)}", status_code=302)


# ── Log mileage (trucks — weekly check-in) ────────────────────────────────────
@router.post("/{task_id}/mileage")
async def log_mileage(
    task_id: int,
    mileage: float = Form(...),
    notes: str     = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    task = db.get(MaintenanceTask, task_id)
    if not task:
        return RedirectResponse("/maintenance", status_code=302)

    today = date.today()
    ml = MileageLog(
        task_id=task_id,
        logged_by_id=user.id,
        log_date=today,
        mileage=mileage,
        notes=notes or None,
    )
    db.add(ml)
    task.current_mileage = mileage
    db.commit()
    return RedirectResponse("/maintenance", status_code=302)


# ── Edit task ─────────────────────────────────────────────────────────────────
@router.get("/{task_id}/edit", response_class=HTMLResponse)
async def edit_task_form(
    task_id: int,
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    task = db.get(MaintenanceTask, task_id)
    if not task:
        return RedirectResponse("/maintenance", status_code=302)
    logs = db.query(MaintenanceLog).filter(
        MaintenanceLog.task_id == task_id
    ).order_by(MaintenanceLog.completed_at.desc()).limit(10).all()
    mileage_logs = db.query(MileageLog).filter(
        MileageLog.task_id == task_id
    ).order_by(MileageLog.log_date.desc()).limit(10).all()

    all_users = db.query(UserModel).filter(UserModel.is_active == True).order_by(UserModel.first_name).all()
    return templates.TemplateResponse("maintenance/detail.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
        "task": task, "logs": logs, "mileage_logs": mileage_logs,
        "equipment_labels": EQUIPMENT_LABELS,
        "freq_labels": FREQ_LABELS,
        "equipment_types": EquipmentType,
        "freq_types": FrequencyType,
        "today": date.today(),
        "all_users": all_users,
    })


@router.post("/{task_id}/edit")
async def update_task(
    request: Request,
    task_id: int,
    name: str              = Form(...),
    equipment_type: str    = Form(...),
    equipment_label: str   = Form(""),
    frequency: str         = Form(...),
    mileage_interval: Optional[float] = Form(None),
    next_due_date: Optional[str]      = Form(None),
    notes: str             = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    task = db.get(MaintenanceTask, task_id)
    if not task:
        return RedirectResponse("/maintenance", status_code=302)
    form_data = await request.form()
    assignee_ids = [int(v) for v in form_data.getlist("assignee_ids") if v]
    task.name = name
    task.equipment_type = EquipmentType(equipment_type)
    task.equipment_label = equipment_label or None
    task.frequency = FrequencyType(frequency)
    task.mileage_interval = mileage_interval
    task.next_due_date = date.fromisoformat(next_due_date) if next_due_date else None
    task.notes = notes or None
    task.assigned_user_ids = assignee_ids or None
    db.commit()
    return RedirectResponse("/maintenance", status_code=302)


@router.post("/{task_id}/deactivate")
async def deactivate_task(
    task_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    task = db.get(MaintenanceTask, task_id)
    if task:
        task.is_active = False
        db.commit()
    return RedirectResponse("/maintenance", status_code=302)


# ── PM Requests ───────────────────────────────────────────────────────────────

@router.get("/requests/new", response_class=HTMLResponse)
async def new_request_form(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    return templates.TemplateResponse("maintenance/request_new.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
        "equipment_types": EquipmentType,
        "equipment_labels": EQUIPMENT_LABELS,
    })


@router.post("/requests/new")
async def submit_request(
    description: str        = Form(...),
    equipment_type: str     = Form(""),
    equipment_label: str    = Form(""),
    notes: str              = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    eq = EquipmentType(equipment_type) if equipment_type else None
    db.add(MaintenanceRequest(
        requested_by_id=user.id,
        equipment_type=eq,
        equipment_label=equipment_label or None,
        description=description,
        notes=notes or None,
    ))
    db.commit()
    from urllib.parse import quote as url_quote
    return RedirectResponse(f"/maintenance?flash={url_quote('PM request submitted — management will review it.')}", status_code=302)


@router.get("/requests", response_class=HTMLResponse)
async def requests_list(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.role.value not in ["owner", "ops_manager"]:
        return RedirectResponse("/maintenance", status_code=302)
    pending = db.query(MaintenanceRequest).filter(
        MaintenanceRequest.status == PMRequestStatus.pending
    ).order_by(MaintenanceRequest.requested_at.desc()).all()
    reviewed = db.query(MaintenanceRequest).filter(
        MaintenanceRequest.status != PMRequestStatus.pending
    ).order_by(MaintenanceRequest.requested_at.desc()).limit(20).all()
    return templates.TemplateResponse("maintenance/requests.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
        "pending": pending,
        "reviewed": reviewed,
        "equipment_labels": EQUIPMENT_LABELS,
        "freq_labels": FREQ_LABELS,
        "equipment_types": EquipmentType,
        "freq_types": FrequencyType,
    })


@router.post("/requests/{req_id}/approve")
async def approve_request(
    request: Request,
    req_id: int,
    frequency: str              = Form(...),
    next_due_date: Optional[str] = Form(None),
    mileage_interval: Optional[float] = Form(None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user or user.role.value not in ["owner", "ops_manager"]:
        return RedirectResponse("/maintenance", status_code=302)
    req = db.get(MaintenanceRequest, req_id)
    if not req:
        return RedirectResponse("/maintenance/requests", status_code=302)

    from datetime import datetime, timezone
    due = date.fromisoformat(next_due_date) if next_due_date else None
    task = MaintenanceTask(
        name=req.description,
        equipment_type=req.equipment_type or EquipmentType.other,
        equipment_label=req.equipment_label,
        frequency=FrequencyType(frequency),
        mileage_interval=mileage_interval,
        next_due_date=due,
        notes=req.notes,
    )
    db.add(task)
    db.flush()

    req.status = PMRequestStatus.approved
    req.reviewed_by_id = user.id
    req.reviewed_at = datetime.now(timezone.utc)
    req.task_id = task.id
    db.commit()
    return RedirectResponse("/maintenance/requests", status_code=302)


@router.post("/requests/{req_id}/dismiss")
async def dismiss_request(
    req_id: int,
    review_note: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user or user.role.value not in ["owner", "ops_manager"]:
        return RedirectResponse("/maintenance", status_code=302)
    req = db.get(MaintenanceRequest, req_id)
    if req:
        from datetime import datetime, timezone
        req.status = PMRequestStatus.dismissed
        req.reviewed_by_id = user.id
        req.reviewed_at = datetime.now(timezone.utc)
        req.review_note = review_note or None
        db.commit()
    return RedirectResponse("/maintenance/requests", status_code=302)
