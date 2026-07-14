import os
import uuid
from fastapi import APIRouter, Depends, Request, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import Optional
from datetime import date, datetime, timezone

DRAWINGS_DIR = "app/static/drawings"


async def save_drawing(form_data, field_name: str = "drawing_file") -> Optional[str]:
    """Extract uploaded file from form data, save it, return the stored filename or None.
    Uses hasattr rather than isinstance — request.form() returns starlette's UploadFile
    which doesn't satisfy isinstance checks against fastapi's UploadFile subclass."""
    field = form_data.get(field_name)
    if hasattr(field, "filename") and field.filename:
        ext = os.path.splitext(field.filename)[1].lower()
        fname = f"{uuid.uuid4().hex}{ext}"
        os.makedirs(DRAWINGS_DIR, exist_ok=True)
        content = await field.read()
        with open(os.path.join(DRAWINGS_DIR, fname), "wb") as fh:
            fh.write(content)
        return fname
    return None

from app.database import get_db
from app.auth import require_user, require_foreman_up, require_management, financials_visible
from app.models.user import User, UserRole
from app.models.order import Order, OrderLineItem, OrderStatus, JobType, Priority
from app.models.customer import Customer
from app.models.production import ProductionStage, StageType, StageStatus, QARecord, QAResult
from app.models.labor import LaborEntry, BillingDept, BILLING_RATES
from app.models.work_session import WorkSession, SessionStatus

router = APIRouter(prefix="/orders", tags=["orders"])
templates = Jinja2Templates(directory="app/templates")

def next_order_number(db: Session) -> str:
    yy = str(date.today().year)[2:]
    prefix = f"VBS-O-{yy}-"
    last = db.query(Order.order_number).filter(
        Order.order_number.like(f"{prefix}%")
    ).order_by(Order.order_number.desc()).first()
    num = (int(last[0].split("-")[-1]) + 1) if last else 1
    return f"{prefix}{num:05d}"

STATUS_FLOW = {
    OrderStatus.draft:         [OrderStatus.confirmed, OrderStatus.cancelled],
    OrderStatus.confirmed:     [OrderStatus.in_production, OrderStatus.on_hold, OrderStatus.cancelled],
    OrderStatus.in_production: [OrderStatus.qa_review, OrderStatus.on_hold, OrderStatus.cancelled],
    OrderStatus.on_hold:       [OrderStatus.cancelled],  # resume option added dynamically from previous_status
    OrderStatus.qa_review:     [OrderStatus.ready, OrderStatus.in_production],
    OrderStatus.ready:         [OrderStatus.delivered],
    OrderStatus.delivered:     [OrderStatus.invoiced],
    OrderStatus.invoiced:      [OrderStatus.paid],
    OrderStatus.paid:          [],
    OrderStatus.cancelled:     [],
}

@router.get("", response_class=HTMLResponse)
async def order_list(
    request: Request,
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    priority: Optional[str] = None,
    q: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = db.query(Order).options(joinedload(Order.customer), joinedload(Order.line_items), joinedload(Order.labor_entries))
    if status == "active":
        query = query.filter(Order.status.notin_([OrderStatus.delivered, OrderStatus.paid, OrderStatus.cancelled]))
    elif status == "wip":
        query = query.filter(Order.status.in_([OrderStatus.in_production, OrderStatus.on_hold, OrderStatus.qa_review, OrderStatus.ready]))
    elif status:
        query = query.filter(Order.status == status)
    if job_type:
        query = query.filter(Order.job_type == job_type)
    if priority:
        query = query.filter(Order.priority == priority)
    if q:
        query = query.join(Customer).filter(
            or_(Order.order_number.ilike(f"%{q}%"), Customer.name.ilike(f"%{q}%"))
        )
    sort_map = {
        "order_number": Order.order_number,
        "status":       Order.status,
        "priority":     Order.priority,
        "promised_date":Order.promised_date,
        "job_type":     Order.job_type,
        "created":      Order.created_at,
        "created_at":   Order.created_at,
    }
    if sort_by and sort_by in sort_map:
        col = sort_map[sort_by]
        query = query.order_by(col.desc() if sort_dir == "desc" else col.asc())
    else:
        query = query.order_by(Order.priority.desc(), Order.promised_date.asc().nulls_last(), Order.created_at.desc())
    orders = query.all()
    if sort_by == "customer":
        orders.sort(key=lambda o: (o.customer.name if o.customer else ""), reverse=(sort_dir == "desc"))
    elif sort_by == "total":
        orders.sort(key=lambda o: sum((li.unit_price or 0) * li.quantity for li in o.line_items), reverse=(sort_dir == "desc"))
    return templates.TemplateResponse("orders/list.html", {
        "request": request, "user": user, "orders": orders,
        "filters": {"status": status, "job_type": job_type, "priority": priority, "q": q},
        "statuses": OrderStatus, "job_types": JobType, "priorities": Priority,
        "sort_by": sort_by, "sort_dir": sort_dir,
        "can_see_financials": financials_visible(user),
    })

@router.get("/new", response_class=HTMLResponse)
async def new_order_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    customers = db.query(Customer).filter(Customer.is_active == True).order_by(Customer.name).all()
    return templates.TemplateResponse("orders/new.html", {
        "request": request, "user": user,
        "customers": customers, "job_types": JobType, "priorities": Priority,
        "today": date.today().isoformat(),
        "can_see_financials": financials_visible(user),
    })

@router.post("/new")
async def create_order(
    request: Request,
    customer_id: int = Form(...), job_type: str = Form(...), priority: str = Form("standard"),
    description: str = Form(""), drawings_required: bool = Form(False), paint_spec: str = Form(""),
    promised_date: str = Form(""), notes: str = Form(""),
    delivery_surcharge: str = Form(""),
    customer_po: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    form = await request.form()
    drawing_filename = await save_drawing(form)

    if not promised_date:
        customers = db.query(Customer).filter(Customer.is_active == True).order_by(Customer.name).all()
        return templates.TemplateResponse("orders/new.html", {
            "request": request, "user": user,
            "customers": customers,
            "job_types": JobType, "priorities": Priority,
            "error": "A promised date is required on every order.",
            "today": date.today().isoformat(),
            "can_see_financials": financials_visible(user),
        }, status_code=422)

    order_num = next_order_number(db)
    order = Order(
        order_number=order_num, customer_id=customer_id, job_type=job_type, priority=priority,
        status=OrderStatus.confirmed, description=description or None, drawings_required=drawings_required,
        drawing_file=drawing_filename,
        customer_po=customer_po.strip() or None,
        paint_spec=paint_spec or None,
        promised_date=date.fromisoformat(promised_date) if promised_date else None,
        notes=notes or None, created_by_id=user.id,
    )
    db.add(order)
    db.flush()
    idx = 0
    while f"li_desc_{idx}" in form:
        desc = form.get(f"li_desc_{idx}", "").strip()
        if desc:
            inv_id_raw = form.get(f"li_inv_id_{idx}", "").strip()
            inv_id = int(inv_id_raw) if inv_id_raw else None
            labor_hrs = float(form.get(f"li_labor_{idx}") or 0) or None
            labor_dept = form.get(f"li_labor_dept_{idx}", "").strip() or None
            db.add(OrderLineItem(
                order_id=order.id, line_number=idx + 1, description=desc,
                quantity=float(form.get(f"li_qty_{idx}", 1) or 1),
                unit=form.get(f"li_unit_{idx}", "").strip() or None,
                material=form.get(f"li_material_{idx}", "").strip() or None,
                unit_price=float(form.get(f"li_price_{idx}") or 0) or None,
                paint_override=form.get(f"li_paint_{idx}", "").strip() or None,
                internal_notes=form.get(f"li_internal_notes_{idx}", "").strip() or None,
                inventory_item_id=inv_id,
                estimated_labor_hours=labor_hrs,
                estimated_labor_dept=labor_dept,
            ))
        idx += 1

    # Delivery surcharge
    surcharge_amount = float(delivery_surcharge) if delivery_surcharge else None
    if surcharge_amount and surcharge_amount > 0:
        db.add(OrderLineItem(
            order_id=order.id, line_number=idx + 1,
            description="Delivery",
            quantity=1, unit_price=surcharge_amount,
            is_delivery_surcharge=True,
        ))

    # Drawings first (review before pulling material), then standard flow
    if drawings_required:
        stage_list = [StageType.drawings, StageType.material_receiving, StageType.fabrication, StageType.qa_qc, StageType.delivery]
    else:
        stage_list = [StageType.material_receiving, StageType.fabrication, StageType.qa_qc, StageType.delivery]
    for s in stage_list:
        db.add(ProductionStage(order_id=order.id, stage_type=s))
    db.commit()
    return RedirectResponse(f"/orders/{order.id}", status_code=302)

@router.get("/{order_id}", response_class=HTMLResponse)
async def order_detail(request: Request, order_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    from app.models.inventory import InventoryItem as InvItem
    order = db.query(Order).options(
        joinedload(Order.customer),
        joinedload(Order.line_items).joinedload(OrderLineItem.inventory_item),
        joinedload(Order.production_stages).joinedload(ProductionStage.assigned_to),
        joinedload(Order.labor_entries).joinedload(LaborEntry.employee),
        joinedload(Order.qa_records), joinedload(Order.drawing_records), joinedload(Order.invoice),
    ).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    sorted_qa = sorted(order.qa_records, key=lambda q: q.inspected_at)
    has_qa_fail = bool(sorted_qa) and sorted_qa[-1].result == QAResult.fail
    next_statuses = list(STATUS_FLOW.get(order.status, []))
    # For on_hold orders, prepend the previous status as the resume option
    if order.status == OrderStatus.on_hold:
        resume = order.previous_status
        if resume is None:
            resume = OrderStatus.in_production if order.labor_entries else OrderStatus.confirmed
        next_statuses = [resume] + next_statuses
    total_labor = sum(e.billed_value for e in order.labor_entries)
    total_hours = sum(e.hours for e in order.labor_entries)
    employees = db.query(User).filter(User.is_active == True).order_by(User.first_name).all()

    # Active work sessions
    active_sessions = db.query(WorkSession).options(
        joinedload(WorkSession.employee)
    ).filter(
        WorkSession.order_id == order_id,
        WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
    ).all()
    active_session_by_stage = {s.stage_id: s for s in active_sessions}

    # Inventory shortage detection (shown when inv_block=1 redirect arrives)
    inv_block = request.query_params.get("inv_block") == "1"
    inv_shortages = []
    if inv_block or order.status == OrderStatus.confirmed:
        for li in order.line_items:
            if li.inventory_item and (li.inventory_item.quantity_on_hand or 0) < (li.quantity or 0):
                inv_shortages.append({
                    "name":  li.inventory_item.name,
                    "sku":   li.inventory_item.sku,
                    "have":  li.inventory_item.quantity_on_hand or 0,
                    "need":  li.quantity,
                    "unit":  li.inventory_item.unit,
                })

    return templates.TemplateResponse("orders/detail.html", {
        "request": request, "user": user, "order": order,
        "next_statuses": next_statuses, "has_qa_fail": has_qa_fail,
        "total_labor": total_labor, "total_hours": total_hours,
        "employees": employees, "billing_depts": BillingDept, "billing_rates": BILLING_RATES,
        "qa_results": QAResult, "stage_types": StageType, "today": date.today().isoformat(),
        "can_see_financials": financials_visible(user),
        "active_session_by_stage": active_session_by_stage,
        "now_utc": datetime.utcnow(),
        "inv_block": inv_block,
        "inv_shortages": inv_shortages,
    })

@router.post("/{order_id}/promised-date")
async def set_promised_date(
    order_id: int, promised_date: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    if user.role != UserRole.owner:
        raise HTTPException(403, "Only the owner can modify promised dates")
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404)
    order.promised_date = date.fromisoformat(promised_date) if promised_date else None
    db.commit()
    return RedirectResponse(f"/orders/{order_id}", status_code=302)

@router.post("/{order_id}/status")
async def update_status(
    order_id: int, new_status: str = Form(...), hold_reason: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    order = db.query(Order).options(
        joinedload(Order.qa_records), joinedload(Order.production_stages), joinedload(Order.invoice),
    ).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404)
    target = OrderStatus(new_status)
    allowed = list(STATUS_FLOW.get(order.status, []))
    # For on_hold, dynamically allow resuming to previous status
    if order.status == OrderStatus.on_hold:
        resume = order.previous_status
        if resume is None:
            resume = OrderStatus.in_production if order.labor_entries else OrderStatus.confirmed
        if resume not in allowed:
            allowed.append(resume)
    if target not in allowed:
        raise HTTPException(400, f"Cannot move from {order.status.value} to {target.value}")
    if target == OrderStatus.on_hold and not hold_reason.strip():
        raise HTTPException(400, "Hold reason required")
    if target == OrderStatus.cancelled and user.role not in [UserRole.owner, UserRole.ops_manager]:
        raise HTTPException(403, "Only management can cancel orders")
    sorted_qa = sorted(order.qa_records, key=lambda q: q.inspected_at)
    has_qa_fail = bool(sorted_qa) and sorted_qa[-1].result == QAResult.fail
    if target == OrderStatus.qa_review and not order.labor_entries:
        raise HTTPException(400, "Cannot send to QA — no labor has been logged for this order")
    if target == OrderStatus.ready and has_qa_fail:
        raise HTTPException(400, "Cannot mark Ready — open QA failure must be resolved first")
    if target == OrderStatus.invoiced and not order.invoice:
        raise HTTPException(400, "Generate the invoice before marking as Invoiced")
    now = datetime.now(timezone.utc)
    # Auto-update production stages based on transition
    if target == OrderStatus.in_production:
        for stage in order.production_stages:
            # Reset blocked qa_qc when going back from qa_review
            if stage.stage_type == StageType.qa_qc and stage.status == StageStatus.blocked:
                stage.status = StageStatus.pending
                stage.started_at = None
            # Auto-advance stages on first entry to production
            if order.status == OrderStatus.confirmed:
                has_drawings = any(
                    s.stage_type == StageType.drawings
                    for s in order.production_stages
                )
                if has_drawings:
                    # Drawings-first: kick off drawings stage, everything else waits
                    if stage.stage_type == StageType.drawings and stage.status == StageStatus.pending:
                        stage.status = StageStatus.in_progress
                        stage.started_at = now
                else:
                    # No drawings: kick off material receiving, fabrication waits
                    if stage.stage_type == StageType.material_receiving and stage.status == StageStatus.pending:
                        stage.status = StageStatus.in_progress
                        stage.started_at = now
    elif target == OrderStatus.qa_review:
        for stage in order.production_stages:
            if stage.stage_type == StageType.fabrication and stage.status == StageStatus.in_progress:
                stage.status = StageStatus.complete
                stage.completed_at = now
            if stage.stage_type == StageType.qa_qc and stage.status == StageStatus.pending:
                stage.status = StageStatus.in_progress
                stage.started_at = now
    elif target == OrderStatus.ready:
        for stage in order.production_stages:
            if stage.stage_type == StageType.qa_qc and stage.status in [StageStatus.in_progress, StageStatus.pending]:
                stage.status = StageStatus.complete
                stage.completed_at = now
    elif target == OrderStatus.delivered:
        for stage in order.production_stages:
            if stage.stage_type == StageType.delivery:
                stage.status = StageStatus.complete
                stage.completed_at = now
    # ── Inventory availability check ──────────�