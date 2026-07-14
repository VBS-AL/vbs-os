from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import require_user
from app.models.user import User
from app.models.order import Order, OrderLineItem
from app.models.packing_list import PackingList, SHIPPED_VIA_LABELS

router = APIRouter(prefix="/orders", tags=["packing"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/{order_id}/packing-list", response_class=HTMLResponse)
async def packing_list_view(
    request: Request,
    order_id: int,
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

    return templates.TemplateResponse("orders/packing_list.html", {
        "request":           request,
        "user":              user,
        "order":             order,
        "pl":                pl,
        "shipped_via_labels": SHIPPED_VIA_LABELS,
    })
