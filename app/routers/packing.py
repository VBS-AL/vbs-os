from fastapi import APIRouter, Depends, Request, HTTPException, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional as _Opt
import os, uuid
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from datetime import datetime, timezone

from app.database import get_db
from app.auth import require_user
from app.models.user import User
from app.models.order import Order, OrderLineItem, OrderStatus
from app.models.packing_list import PackingList, SHIPPED_VIA_LABELS
from app.models.production import ProductionStage, StageStatus, StageType
from app.models.work_session import WorkSession, SessionStatus
from app.models.labor import LaborEntry, BillingDept
from app.routers.production import _net_minutes, EASTERN

router = APIRouter(prefix="/orders", tags=["packing"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/{order_id}/packing-list", response_class=HTMLResponse)
async def packing_list_view(
    request: Request,
    order_id: int,
    error: str = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    order = db.query(Order).options(
        joinedload(Order.customer),
        joinedload(Order.line_items).joinedload(OrderLineItem.inventory_item),
        joinedload(Order.packing_list),
    ).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(404, "Order not found")

    pl = order.packing_list

    # Backfill pl_number for any packing list that was created before the column existed
    if pl and not pl.pl_number:
        from datetime import date as _d
        yy      = str(_d.today().year)[2:]
        pattern = f"VBS-PL-{yy}-"
        last    = db.query(PackingList.pl_number).filter(
            PackingList.pl_number.like(f"{pattern}%")
        ).order_by(PackingList.pl_number.desc()).first()
        next_n  = (int(last[0].split("-")[-1]) + 1) if (last and last[0]) else 1
        pl.pl_number = f"{pattern}{next_n:05d}"
        db.commit()

    return templates.TemplateResponse("orders/packing_list.html", {
        "request":            request,
        "user":               user,
        "order":              order,
        "pl":                 pl,
        "shipped_via_labels": SHIPPED_VIA_LABELS,
        "error":              error,
    })


@router.post("/{order_id}/packing-list/confirm-check")
async def confirm_packing_check(
    order_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """The designated checker confirms they have physically reviewed the packing list."""
    order = db.query(Order).options(joinedload(Order.packing_list)).filter(Order.id == order_id).first()
    if not order or not order.packing_list:
        raise HTTPException(404, "Packing list not found")
    pl = order.packing_list
    if pl.checker_id != user.id:
        raise HTTPException(403, "Only the designated checker can confirm this packing list")
    pl.check_confirmed    = True
    pl.check_confirmed_at = datetime.utcnow()
    db.commit()
    return RedirectResponse("/production/queue", status_code=302)


@router.post("/{order_id}/confirm-delivery")
async def confirm_delivery(
    order_id: int,
    override: bool = Form(False),
    photo: _Opt[UploadFile] = File(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    order = db.query(Order).options(joinedload(Order.packing_list)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")

    # Allow any status at or past ready — a timer can still be open even if the order was
    # invoiced or paid before the driver closed their session
    _CLOSEABLE_STATUSES = (
        OrderStatus.ready, OrderStatus.delivered,
        OrderStatus.invoiced, OrderStatus.paid,
    )
    if order.status not in _CLOSEABLE_STATUSES:
        return RedirectResponse(f"/orders/{order_id}/packing-list?error=Order+is+not+in+a+ready+status", status_code=302)

    pl = order.packing_list
    is_pickup = order.preferred_delivery_method == "customer_pickup"
    can_override = override and user.role.value in ("owner", "ops_manager")
    if pl and pl.checker_id and not pl.check_confirmed and not can_override:
        action = "pickup" if is_pickup else "delivery"
        return RedirectResponse(
            f"/orders/{order_id}/packing-list?error=Packing+list+must+be+confirmed+by+the+checker+before+{action}+can+be+completed",
            status_code=302,
        )

    now = datetime.utcnow()
    if order.status == OrderStatus.ready:
        order.status = OrderStatus.delivered

    # Complete the delivery stage (if not already) and stop any running timer
    delivery_stage = db.query(ProductionStage).filter(
        ProductionStage.order_id == order_id,
        ProductionStage.stage_type == StageType.delivery,
    ).first()
    if delivery_stage:
        if delivery_stage.status != StageStatus.complete:
            delivery_stage.status       = StageStatus.complete
            delivery_stage.completed_at = now

        open_session = db.query(WorkSession).filter(
            WorkSession.stage_id == delivery_stage.id,
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
                    billed_value = 0.0,
                    work_date    = work_date,
                    notes        = open_session.notes,
                    is_rework    = 0,
                )
                db.add(entry)
                db.flush()
                open_session.labor_entry_id = entry.id

    # Save delivery proof photo if provided
    if photo and photo.filename:
        ext = os.path.splitext(photo.filename)[-1].lower() or ".jpg"
        fname = f"{order_id}_{uuid.uuid4().hex[:8]}{ext}"
        upload_dir = "app/static/uploads/delivery"
        os.makedirs(upload_dir, exist_ok=True)
        content = await photo.read()
        with open(f"{upload_dir}/{fname}", "wb") as f:
            f.write(content)
        pl = order.packing_list
        if pl:
            pl.delivery_photo_path = f"uploads/delivery/{fname}"

    db.commit()
    return RedirectResponse(f"/orders/{order_id}/packing-list", status_code=302)
