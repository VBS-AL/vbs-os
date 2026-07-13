from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from datetime import date, timedelta

from app.database import get_db
from app.auth import get_current_user, financials_visible
from app.models.order import Order, OrderStatus, OrderLineItem
from app.models.invoice import Invoice, Payment, PaymentStatus
from app.models.quote import Quote, QuoteStatus
from app.models.customer import Customer
from app.models.labor import LaborEntry, BillingDept, BILLING_RATES
from app.models.settings import AppSetting
from app.models.user import UserRole

router = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(directory="app/templates")

PERIOD_LABELS = {
    "mtd": "Month to Date",
    "qtd": "Quarter to Date",
    "ytd": "Year to Date",
    "30d": "Last 30 Days",
    "90d": "Last 90 Days",
}

def get_start_date(period: str) -> date:
    today = date.today()
    if period == "qtd":
        q_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q_month, day=1)
    elif period == "ytd":
        return today.replace(month=1, day=1)
    elif period == "30d":
        return today - timedelta(days=30)
    elif period == "90d":
        return today - timedelta(days=90)
    return today.replace(day=1)  # mtd default


@router.get("", response_class=HTMLResponse)
async def reports_index(
    request: Request,
    period: str = "mtd",
    date_from: str = None,
    date_to: str = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.role not in [UserRole.owner, UserRole.ops_manager]:
        return RedirectResponse("/dashboard", status_code=302)

    today = date.today()
    end_date = today

    if date_from and date_to:
        try:
            start_date = date.fromisoformat(date_from)
            end_date   = date.fromisoformat(date_to)
            period     = "custom"
        except ValueError:
            date_from = date_to = None
            period = "mtd"
            start_date = get_start_date(period)
    else:
        if period not in PERIOD_LABELS:
            period = "mtd"
        start_date = get_start_date(period)

    period_labels = dict(PERIOD_LABELS)
    if period == "custom":
        period_labels["custom"] = f"{date_from} – {date_to}"

    # ── Revenue ───────────────────────────────────────────────────────────
    revenue_collected = db.query(func.sum(Payment.amount)).filter(
        Payment.payment_date >= start_date,
        Payment.payment_date <= end_date,
    ).scalar() or 0.0

    revenue_invoiced = db.query(func.sum(Invoice.total)).filter(
        Invoice.invoice_date >= start_date,
        Invoice.invoice_date <= end_date,
    ).scalar() or 0.0

    outstanding = db.query(func.sum(Invoice.balance_due)).filter(
        Invoice.payment_status.notin_([PaymentStatus.paid, PaymentStatus.void])
    ).scalar() or 0.0

    overdue = db.query(func.sum(Invoice.balance_due)).filter(
        Invoice.payment_status == PaymentStatus.overdue
    ).scalar() or 0.0

    # ── Orders summary ────────────────────────────────────────────────────
    orders_in_period = db.query(Order).filter(
        func.date(Order.created_at) >= start_date,
        func.date(Order.created_at) <= end_date,
    ).all()

    orders_created   = len(orders_in_period)
    orders_completed = sum(1 for o in orders_in_period if o.status in [OrderStatus.delivered, OrderStatus.paid])
    orders_cancelled = sum(1 for o in orders_in_period if o.status == OrderStatus.cancelled)
    orders_active    = sum(1 for o in orders_in_period if o.status not in [
        OrderStatus.delivered, OrderStatus.paid, OrderStatus.cancelled
    ])

    # Avg order value (from invoices in period)
    inv_rows = db.query(Invoice.total).filter(
        Invoice.invoice_date >= start_date,
        Invoice.invoice_date <= end_date,
    ).all()
    avg_order_value = (sum(r[0] for r in inv_rows) / len(inv_rows)) if inv_rows else 0.0

    # ── Quote performance ─────────────────────────────────────────────────
    quotes_in_period = db.query(Quote).filter(
        func.date(Quote.created_at) >= start_date,
        func.date(Quote.created_at) <= end_date,
    ).all()
    q_total     = len(quotes_in_period)
    q_sent      = sum(1 for q in quotes_in_period if q.status != QuoteStatus.draft)
    q_converted = sum(1 for q in quotes_in_period if q.status == QuoteStatus.converted)
    q_declined  = sum(1 for q in quotes_in_period if q.status == QuoteStatus.declined)
    q_expired   = sum(1 for q in quotes_in_period if q.status == QuoteStatus.expired)
    conversion_rate = round(q_converted / q_sent * 100) if q_sent > 0 else None

    # ── Top customers by revenue (invoices) ───────────────────────────────
    top_customers_raw = db.query(
        Customer.id,
        Customer.name,
        func.count(Invoice.id).label("invoice_count"),
        func.sum(Invoice.total).label("total_invoiced"),
        func.sum(Invoice.amount_paid).label("total_paid"),
    ).join(Order, Order.customer_id == Customer.id)\
     .join(Invoice, Invoice.order_id == Order.id)\
     .filter(Invoice.invoice_date >= start_date, Invoice.invoice_date <= end_date)\
     .group_by(Customer.id, Customer.name)\
     .order_by(func.sum(Invoice.total).desc())\
     .limit(10).all()

    # ── Labor by department ───────────────────────────────────────────────
    labor_rows = db.query(
        LaborEntry.billing_dept,
        func.sum(LaborEntry.hours).label("total_hours"),
        func.sum(LaborEntry.billed_value).label("total_value"),
    ).filter(
        func.date(LaborEntry.created_at) >= start_date,
        func.date(LaborEntry.created_at) <= end_date,
    ).group_by(LaborEntry.billing_dept).all()

    dept_labels = {
        "fab":      "Fabrication",
        "weld":     "Welding",
        "paint":    "Paint",
        "install":  "Installation",
        "other":    "Other",
    }

    return templates.TemplateResponse("reports/index.html", {
        "request":          request,
        "user":             user,
        "can_see_financials": True,  # already gated above
        "period":           period,
        "period_label":     period_labels[period],
        "pe