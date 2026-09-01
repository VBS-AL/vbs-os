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
from app.models.order import Order, OrderLineItem, OrderStatus, JobType, Priority, StagingLocation
from app.models.labor import BILLING_RATES, BillingDept
from app.models.customer import Customer
from app.models.inventory import InventoryItem
from app.models.production import ProductionStage, StageType, StageStatus, QARecord, QAResult, DrawingRecord
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
    query = db.query(Order).options(joinedload(Order.customer), joinedload(Order.line_items), joinedload(Order.labor_entries), joinedload(Order.invoice))
    # Determine active tab (drives both the filter and the template highlight)
    if not status or status == "active":
        active_tab = "active"
        query = query.filter(Order.status.notin_([
            OrderStatus.invoiced, OrderStatus.paid, OrderStatus.cancelled
        ]))
    elif status == "invoiced":
        active_tab = "invoiced"
        query = query.filter(Order.status == OrderStatus.invoiced)
    elif status == "paid":
        active_tab = "paid"
        query = query.filter(Order.status == OrderStatus.paid)
    elif status == "all":
        active_tab = "all"
        # no status filter
    elif status == "wip":
        active_tab = None
        query = query.filter(Order.status.in_([OrderStatus.in_production, OrderStatus.on_hold, OrderStatus.qa_review, OrderStatus.ready]))
    else:
        active_tab = None
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
        "active_tab": active_tab,
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
    preferred_delivery_method: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    form = await request.form()

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
        drawing_file=None,
        customer_po=customer_po.strip() or None,
        paint_spec=paint_spec or None,
        preferred_delivery_method=preferred_delivery_method or None,
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
            # For retail orders, auto-calculate unit price from inventory cost × markup
            form_price = float(form.get(f"li_price_{idx}") or 0) or None
            if order.job_type == JobType.retail and inv_id and not form_price:
                inv_item = db.query(InventoryItem).filter(InventoryItem.id == inv_id).first()
                if inv_item and inv_item.cost_per_unit:
                    markup = inv_item.retail_markup if inv_item.retail_markup else 3.0
                    form_price = round(inv_item.cost_per_unit * markup, 2)
            # Freeze labor rate at time of creation
            rate_snapshot = None
            if labor_dept:
                try:
                    rate_snapshot = BILLING_RATES.get(BillingDept(labor_dept))
                except Exception:
                    pass
            db.add(OrderLineItem(
                order_id=order.id, line_number=idx + 1, description=desc,
                quantity=float(form.get(f"li_qty_{idx}", 1) or 1),
                unit=form.get(f"li_unit_{idx}", "").strip() or None,
                material=form.get(f"li_material_{idx}", "").strip() or None,
                unit_price=form_price,
                paint_override=form.get(f"li_paint_{idx}", "").strip() or None,
                internal_notes=form.get(f"li_internal_notes_{idx}", "").strip() or None,
                inventory_item_id=inv_id,
                estimated_labor_hours=labor_hrs,
                estimated_labor_dept=labor_dept,
                labor_rate_snapshot=rate_snapshot,
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

    # Retail orders (counter/walk-in sales) skip production entirely — ready to invoice immediately
    if order.job_type == JobType.retail:
        order.status = OrderStatus.ready
    else:
        # Drawings first (review before pulling material), then standard flow
        if drawings_required:
            stage_list = [StageType.drawings, StageType.material_receiving, StageType.fabrication, StageType.qa_qc, StageType.delivery]
        else:
            stage_list = [StageType.material_receiving, StageType.fabrication, StageType.qa_qc, StageType.delivery]
        for s in stage_list:
            db.add(ProductionStage(order_id=order.id, stage_type=s))

    # Save any uploaded drawing files as DrawingRecord entries
    from app.models.production import DrawingStatus
    uploaded_files = form.getlist("files")
    if uploaded_files:
        dest_dir = os.path.join(DRAWINGS_DIR, order_num)
        os.makedirs(dest_dir, exist_ok=True)
        allowed_ext = {".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".tiff", ".xlsx", ".docx"}
        for upload in uploaded_files:
            if not hasattr(upload, "filename") or not upload.filename:
                continue
            ext = os.path.splitext(upload.filename)[1].lower()
            if ext not in allowed_ext:
                continue
            timestamp = int(datetime.now(timezone.utc).timestamp())
            unique_filename = f"{timestamp}_{upload.filename}"
            content = await upload.read()
            with open(os.path.join(dest_dir, unique_filename), "wb") as fh:
                fh.write(content)
            db.add(DrawingRecord(
                order_id=order.id,
                drawing_type="drawing",
                file_reference=unique_filename,
                display_name=upload.filename,
                uploaded_by_id=user.id,
                status=DrawingStatus.pending,
            ))

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
        joinedload(Order.qa_records).joinedload(QARecord.inspector),
        joinedload(Order.drawing_records).joinedload(DrawingRecord.uploaded_by),
        joinedload(Order.invoice),
        joinedload(Order.remnant_records),
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
    # Stage → actual hours map for production history display
    stage_hours: dict[int, float] = {}
    for e in order.labor_entries:
        if e.stage_id:
            stage_hours[e.stage_id] = stage_hours.get(e.stage_id, 0.0) + (e.hours or 0.0)
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
        "stage_hours": stage_hours,
        "employees": employees, "billing_depts": BillingDept, "billing_rates": BILLING_RATES,
        "qa_results": QAResult, "stage_types": StageType, "today": date.today().isoformat(),
        "can_see_financials": financials_visible(user),
        "active_session_by_stage": active_session_by_stage,
        "now_utc": datetime.now(timezone.utc),
        "inv_block": inv_block,
        "inv_shortages": inv_shortages,
        "status_error": request.query_params.get("status_error", ""),
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

@router.post("/{order_id}/third-party")
async def add_third_party_line(
    order_id: int,
    description: str  = Form(...),
    vendor_name: str  = Form(""),
    vendor_cost: float = Form(...),
    markup_pct: float = Form(0.30),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Add a 3rd party outsourced service line item to an order."""
    order = db.get(Order, order_id)
    if not order:
        return RedirectResponse(f"/orders/{order_id}", status_code=302)
    billable = round(vendor_cost * (1 + markup_pct), 2)
    desc = f"{description}{' — ' + vendor_name if vendor_name else ''}"
    max_line = max((li.line_number for li in order.line_items), default=0)
    db.add(OrderLineItem(
        order_id=order_id,
        line_number=max_line + 1,
        description=desc,
        quantity=1,
        unit_price=billable,
        third_party_cost=vendor_cost,
        third_party_markup=markup_pct,
    ))
    db.commit()
    return RedirectResponse(f"/orders/{order_id}", status_code=302)


@router.post("/{order_id}/line-items")
async def add_line_item(
    order_id: int,
    description: str  = Form(...),
    quantity: float   = Form(1),
    unit_price: str   = Form(""),
    est_labor_hours: str = Form(""),
    est_labor_dept: str  = Form(""),
    internal_notes: str  = Form(""),
    inventory_item_id: str = Form(""),
    user: User = Depends(require_foreman_up),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    if order.invoice:
        raise HTTPException(400, "Cannot modify an invoiced order")
    max_line = max((li.line_number for li in order.line_items), default=0)
    inv_id = int(inventory_item_id) if inventory_item_id.strip() else None
    li = OrderLineItem(
        order_id=order_id,
        line_number=max_line + 1,
        description=description.strip(),
        quantity=quantity,
        unit_price=float(unit_price) if unit_price.strip() else None,
        estimated_labor_hours=float(est_labor_hours) if est_labor_hours.strip() else None,
        estimated_labor_dept=est_labor_dept.strip() or None,
        internal_notes=internal_notes.strip() or None,
        inventory_item_id=inv_id,
    )
    db.add(li)
    db.commit()
    return RedirectResponse(f"/orders/{order_id}#line-items", status_code=302)


@router.post("/{order_id}/line-items/{li_id}/edit")
async def edit_line_item(
    order_id: int,
    li_id: int,
    description: str  = Form(...),
    quantity: float   = Form(1),
    unit_price: str   = Form(""),
    est_labor_hours: str = Form(""),
    est_labor_dept: str  = Form(""),
    internal_notes: str  = Form(""),
    user: User = Depends(require_foreman_up),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    if order.invoice:
        raise HTTPException(400, "Cannot modify an invoiced order")
    li = db.get(OrderLineItem, li_id)
    if not li or li.order_id != order_id:
        raise HTTPException(404, "Line item not found")
    li.description          = description.strip()
    li.quantity             = quantity
    li.unit_price           = float(unit_price) if unit_price.strip() else None
    li.estimated_labor_hours = float(est_labor_hours) if est_labor_hours.strip() else None
    li.estimated_labor_dept  = est_labor_dept.strip() or None
    li.internal_notes        = internal_notes.strip() or None
    db.commit()
    return RedirectResponse(f"/orders/{order_id}#line-items", status_code=302)


@router.post("/{order_id}/line-items/{li_id}/delete")
async def delete_line_item(
    order_id: int,
    li_id: int,
    user: User = Depends(require_foreman_up),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    if order.invoice:
        raise HTTPException(400, "Cannot modify an invoiced order")
    li = db.get(OrderLineItem, li_id)
    if not li or li.order_id != order_id or li.is_delivery_surcharge:
        raise HTTPException(404, "Line item not found or cannot be deleted")
    db.delete(li)
    db.commit()
    return RedirectResponse(f"/orders/{order_id}#line-items", status_code=302)


@router.post("/{order_id}/staging")
async def update_staging(
    order_id: int,
    staging_location: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    from app.models.order import StagingLocation
    order = db.get(Order, order_id)
    if order:
        order.staging_location = StagingLocation(staging_location) if staging_location else None
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
    if target == OrderStatus.in_production and order.job_type != JobType.retail:
        if not order.production_stages:
            raise HTTPException(400, "Add production stages before starting production")
        unassigned = [s for s in order.production_stages if not s.assigned_to_id]
        if unassigned:
            names = ", ".join(s.stage_type.value.replace("_", " ").title() for s in unassigned)
            raise HTTPException(400, f"Assign workers to all stages before starting production: {names}")
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
        # Block if someone is actively clocked in on the delivery stage — they must confirm from their queue
        delivery_stage = next(
            (s for s in order.production_stages if s.stage_type == StageType.delivery), None
        )
        if delivery_stage:
            active_delivery_session = db.query(WorkSession).options(
                joinedload(WorkSession.employee)
            ).filter(
                WorkSession.stage_id == delivery_stage.id,
                WorkSession.status.in_([SessionStatus.active, SessionStatus.paused]),
            ).first()
            if active_delivery_session and active_delivery_session.employee:
                emp = active_delivery_session.employee
                name = f"{emp.first_name} {emp.last_name or ''}".strip()
                from urllib.parse import quote
                return RedirectResponse(
                    f"/orders/{order_id}?status_error={quote(f'{name} is clocked in on Delivery — they need to confirm delivery from their queue.')}",
                    status_code=302,
                )
        for stage in order.production_stages:
            if stage.stage_type == StageType.delivery:
                stage.status = StageStatus.complete
                stage.completed_at = now
    # ── Inventory availability check ──────────────────────────────────────
    from app.models.inventory import InventoryItem as InvItem, InventoryAdjustment, AdjustmentReason
    if target == OrderStatus.in_production and order.status == OrderStatus.confirmed:
        shortages = []
        for li in order.line_items:
            if li.inventory_item_id:
                inv = db.query(InvItem).filter(InvItem.id == li.inventory_item_id).first()
                if inv and (inv.quantity_on_hand or 0) < (li.quantity or 0):
                    shortages.append(inv.sku or inv.name)
        if shortages:
            return RedirectResponse(
                f"/orders/{order_id}?inv_block=1",
                status_code=302,
            )

    # ── Inventory auto-deduct / auto-reverse ──────────────────────────────
    if target == OrderStatus.in_production and order.status == OrderStatus.confirmed:
        # Deduct stock for all inventory-linked line items
        for li in order.line_items:
            if li.inventory_item_id:
                inv = db.query(InvItem).filter(InvItem.id == li.inventory_item_id).first()
                if inv:
                    inv.quantity_on_hand = (inv.quantity_on_hand or 0) - li.quantity
                    db.add(InventoryAdjustment(
                        item_id=inv.id,
                        delta=-li.quantity,
                        reason=AdjustmentReason.used,
                        order_id=order.id,
                        notes=f"Auto-deducted for order {order.order_number}",
                        recorded_by_id=user.id,
                    ))
    if target == OrderStatus.cancelled:
        # Reverse any prior deductions tied to this order
        prior = db.query(InventoryAdjustment).filter(
            InventoryAdjustment.order_id == order.id,
            InventoryAdjustment.reason == AdjustmentReason.used,
            InventoryAdjustment.delta < 0,
        ).all()
        for deduction in prior:
            inv = db.query(InvItem).filter(InvItem.id == deduction.item_id).first()
            if inv:
                inv.quantity_on_hand = (inv.quantity_on_hand or 0) + abs(deduction.delta)
                db.add(InventoryAdjustment(
                    item_id=inv.id,
                    delta=abs(deduction.delta),
                    reason=AdjustmentReason.correction,
                    order_id=order.id,
                    notes=f"Auto-reversed: order {order.order_number} cancelled",
                    recorded_by_id=user.id,
                ))

    if target == OrderStatus.on_hold:
        order.previous_status = order.status  # remember where we came from
        order.hold_reason = hold_reason.strip()
        order.hold_owner = f"{user.first_name} {user.last_name}"
    else:
        if order.status == OrderStatus.on_hold:
            order.hold_reason = None
            order.hold_owner = None
            order.previous_status = None
    order.status = target
    if target == OrderStatus.delivered:
        order.ship_date = date.today()
    db.commit()
    return RedirectResponse(f"/orders/{order_id}", status_code=302)

@router.post("/{order_id}/labor")
async def log_labor(
    order_id: int, billing_dept: str = Form(...), hours: float = Form(...),
    work_date: str = Form(...), is_rework: int = Form(0), notes: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404)
    dept = BillingDept(billing_dept)
    rate = BILLING_RATES[dept]
    db.add(LaborEntry(
        order_id=order_id, employee_id=user.id, billing_dept=dept, hours=hours,
        billing_rate=rate, billed_value=round(hours * rate, 2),
        work_date=date.fromisoformat(work_date), is_rework=int(is_rework),
        notes=notes.strip() or None,
    ))
    db.commit()
    return RedirectResponse(f"/orders/{order_id}#labor", status_code=302)

@router.post("/{order_id}/qa")
async def log_qa(
    order_id: int, result: str = Form(...), failure_reason: str = Form(""),
    rework_notes: str = Form(""), certified_weld: bool = Form(False), cert_reference: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    order = db.query(Order).options(joinedload(Order.production_stages)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404)
    if order.status != OrderStatus.qa_review:
        raise HTTPException(400, "QA records can only be logged when the order is in QA / Inspection status")
    result_enum = QAResult(result)
    if result_enum == QAResult.fail:
        if not failure_reason.strip():
            raise HTTPException(400, "Failure reason is required when result is Fail")
        if not rework_notes.strip():
            raise HTTPException(400, "Rework notes are required when result is Fail")
    elif result_enum == QAResult.conditional:
        if not failure_reason.strip():
            raise HTTPException(400, "A condition note is required for Conditional Pass")
    db.add(QARecord(
        order_id=order_id, inspector_id=user.id, result=result_enum,
        failure_reason=failure_reason.strip() or None, rework_notes=rework_notes.strip() or None,
        certified_weld=certified_weld, cert_reference=cert_reference.strip() or None,
    ))
    now_qa = datetime.now(timezone.utc)
    if result_enum == QAResult.fail:
        for stage in order.production_stages:
            if stage.stage_type == StageType.qa_qc:
                stage.status = StageStatus.blocked
        order.rework_count = (order.rework_count or 0) + 1
        db.add(ProductionStage(
            order_id=order_id, stage_type=StageType.fabrication,
            notes=f"Rework — Cycle {order.rework_count}",
        ))
    elif result_enum in [QAResult.pass_result, QAResult.conditional]:
        for stage in order.production_stages:
            if stage.stage_type == StageType.qa_qc and stage.status in [StageStatus.in_progress, StageStatus.blocked, StageStatus.pending]:
                stage.status = StageStatus.complete
                stage.completed_at = now_qa
    db.commit()
    return RedirectResponse(f"/orders/{order_id}#qa", status_code=302)


@router.get("/search/customers", response_class=HTMLResponse)
async def customer_search(
    _customer_search: str = "", user: User = Depends(require_user), db: Session = Depends(get_db),
):
    q = _customer_search.strip()
    if len(q) < 2:
        return HTMLResponse("")
    results = db.query(Customer).filter(
        (Customer.name.ilike(f"%{q}%") | Customer.phone.ilike(f"%{q}%") | Customer.company.ilike(f"%{q}%")),
        Customer.is_active == True,
    ).limit(8).all()
    if not results:
        return HTMLResponse('<div class="px-3 py-2 text-sm text-gray-400">No customers found</div>')
    html = ""
    for c in results:
        label = c.display_name  # company if set, otherwise name
        sub   = f'  <span class="text-gray-400 text-xs">{c.name}</span>' if c.company else ""
        phone = f'  <span class="text-gray-400 text-xs">{c.phone}</span>' if c.phone else ""
        html += f'<div data-customer-id="{c.id}" data-customer-name="{label}" class="px-3 py-2 cursor-pointer hover:bg-steel-light text-sm">{label}{sub}{phone}</div>'
    return HTMLResponse(html)
