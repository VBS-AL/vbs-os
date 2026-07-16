from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, or_

from datetime import date

from app.database import get_db
from app.auth import require_user
from app.models.user import User
from app.models.order import Order, OrderStatus
from app.models.customer import Customer
from app.models.packing_list import PackingList, SHIPPED_VIA_LABELS

router = APIRouter(prefix="/deliveries", tags=["deliveries"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def deliveries_index(
    request: Request,
    q: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(PackingList)
        .options(
            joinedload(PackingList.order).joinedload(Order.customer),
            joinedload(PackingList.created_by),
            joinedload(PackingList.checker),
        )
        .join(PackingList.order)
        .join(Order.customer, isouter=True)
    )

    # Status filter
    if status == "awaiting_check":
        query = query.filter(
            PackingList.checker_id != None,
            PackingList.check_confirmed == False,
        )
    elif status == "ready":
        query = query.filter(
            Order.status != OrderStatus.delivered,
            or_(PackingList.checker_id == None, PackingList.check_confirmed == True),
        )
    elif status == "delivered":
        query = query.filter(Order.status == OrderStatus.delivered)

    # Search
    if q:
        query = query.filter(
            or_(
                PackingList.pl_number.ilike(f"%{q}%"),
                Order.order_number.ilike(f"%{q}%"),
                Customer.name.ilike(f"%{q}%"),
            )
        )

    # Date range on date_shipped
    if date_from:
        try:
            query = query.filter(PackingList.date_shipped >= date.fromisoformat(date_from))
        except ValueError:
            date_from = ""
    if date_to:
        try:
            query = query.filter(PackingList.date_shipped <= date.fromisoformat(date_to))
        except ValueError:
            date_to = ""

    packing_lists = query.order_by(desc(PackingList.created_at)).all()

    return templates.TemplateResponse("deliveries/index.html", {
        "request":            request,
        "user":               user,
        "packing_lists":      packing_lists,
        "shipped_via_labels": SHIPPED_VIA_LABELS,
        "filters": {
            "q":         q,
            "status":    status,
            "date_from": date_from,
            "date_to":   date_to,
        },
    })
