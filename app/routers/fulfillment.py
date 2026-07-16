from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, or_, exists

from datetime import date

from app.database import get_db
from app.auth import require_user, financials_visible
from app.models.user import User
from app.models.order import Order, OrderStatus
from app.models.customer import Customer
from app.models.packing_list import PackingList, SHIPPED_VIA_LABELS

router = APIRouter(prefix="/fulfillment", tags=["fulfillment"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def fulfillment_index(
    request: Request,
    q: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    # ── Section 1: orders with a packing list, not yet invoiced ──────────────
    pl_query = (
        db.query(PackingList)
        .options(
            joinedload(PackingList.order).joinedload(Order.customer),
            joinedload(PackingList.created_by),
            joinedload(PackingList.checker),
        )
        .join(PackingList.order)
        .join(Order.customer, isouter=True)
        .filter(Order.status.in_([OrderStatus.ready, OrderStatus.delivered]))
    )

    # Status filter
    if status == "awaiting_check":
        pl_query = pl_query.filter(
            PackingList.checker_id != None,
            PackingList.check_confirmed == False,
        )
    elif status == "ready":
        pl_query = pl_query.filter(
            Order.status == OrderStatus.ready,
            or_(PackingList.checker_id == None, PackingList.check_confirmed == True),
        )
    elif status == "fulfilled":
        pl_query = pl_query.filter(Order.status == OrderStatus.delivered)

    # Search
    if q:
        pl_query = pl_query.filter(
            or_(
                PackingList.pl_number.ilike(f"%{q}%"),
                Order.order_number.ilike(f"%{q}%"),
                Customer.name.ilike(f"%{q}%"),
            )
        )

    # Date range on date_shipped
    if date_from:
        try:
            pl_query = pl_query.filter(PackingList.date_shipped >= date.fromisoformat(date_from))
        except ValueError:
            date_from = ""
    if date_to:
        try:
            pl_query = pl_query.filter(PackingList.date_shipped <= date.fromisoformat(date_to))
        except ValueError:
            date_to = ""

    packing_lists = pl_query.order_by(desc(PackingList.created_at)).all()

    # ── Section 2: ready orders with no packing list yet ─────────────────────
    awaiting_pl = (
        db.query(Order)
        .options(joinedload(Order.customer))
        .filter(
            Order.status == OrderStatus.ready,
            ~exists().where(PackingList.order_id == Order.id),
        )
        .order_by(Order.promised_date.asc().nulls_last(), Order.created_at.asc())
        .all()
    )

    return templates.TemplateResponse("fulfillment/index.html", {
        "request":            request,
        "user":               user,
        "can_see_financials": financials_visible(user),
        "packing_lists":      packing_lists,
        "awaiting_pl":        awaiting_pl,
        "shipped_via_labels": SHIPPED_VIA_LABELS,
        "today":              date.today(),
        "filters": {
            "q":         q,
            "status":    status,
            "date_from": date_from,
            "date_to":   date_to,
        },
    })
