from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from datetime import date, timedelta

from app.database import get_db
from app.auth import get_current_user, financials_visible
from app.models.order import Order, OrderStatus, OrderLineItem, JobType
from app.models.invoice import Invoice, Payment, PaymentStatus
from app.models.quote import Quote, QuoteStatus
from app.models.customer import Customer
from app.models.labor import LaborEntry, BillingDept, BILLING_RATES
from app.models.packing_list import PackingList, ShippedVia, SHIPPED_VIA_LABELS
from app.models.production import ProductionStage, StageType, StageStatus
from sqlalchemy import or_, case
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

    # ── Fulfillment stats ─────────────────────────────────────────────────
    pls_in_period = db.query(PackingList).options(
        joinedload(PackingList.order),
    ).join(PackingList.order).filter(
        PackingList.date_shipped != None,
        PackingList.date_shipped >= start_date,
        PackingList.date_shipped <= end_date,
    ).all()

    # On-time rate: date_shipped <= promised_date
    total_with_promise = [
        pl for pl in pls_in_period
        if pl.date_shipped and pl.order.promised_date
    ]
    on_time = [
        pl for pl in total_with_promise
        if pl.date_shipped <= pl.order.promised_date
    ]
    on_time_rate = round(len(on_time) / len(total_with_promise) * 100) if total_with_promise else None
    on_time_count = len(on_time)
    total_with_promise_count = len(total_with_promise)

    # Avg full cycle time: order created_at → date_shipped
    cycle_times = []
    for pl in pls_in_period:
        if pl.date_shipped and pl.order.created_at:
            created = pl.order.created_at.date() if hasattr(pl.order.created_at, 'date') else pl.order.created_at
            delta = (pl.date_shipped - created).days
            if delta >= 0:
                cycle_times.append(delta)
    avg_cycle_days = round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else None
    min_cycle_days = min(cycle_times) if cycle_times else None
    max_cycle_days = max(cycle_times) if cycle_times else None

    # Delivery method breakdown
    method_counts = {}
    for pl in pls_in_period:
        via = pl.shipped_via.value if pl.shipped_via else "unknown"
        method_counts[via] = method_counts.get(via, 0) + 1

    method_labels = {
        "vbs_delivery":        "VBS Delivery",
        "customer_pickup":     "Customer Pickup",
        "third_party_freight": "3rd Party Freight",
        "courier":             "Courier",
        "other":               "Other",
        "unknown":             "Not Specified",
    }
    method_rows = sorted(
        [{"via": k, "label": method_labels.get(k, k), "count": v} for k, v in method_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )
    total_fulfilled = len(pls_in_period)

    # ── Retail vs fabrication revenue breakdown ───────────────────────────
    retail_orders = db.query(Order).options(
        joinedload(Order.line_items),
    ).filter(
        Order.job_type == JobType.retail,
        func.date(Order.created_at) >= start_date,
        func.date(Order.created_at) <= end_date,
    ).all()

    fab_orders = [o for o in orders_in_period if o.job_type != JobType.retail]

    retail_order_count = len(retail_orders)
    fab_order_count    = len(fab_orders)

    retail_revenue_raw = db.query(func.sum(Invoice.total)).join(
        Order, Invoice.order_id == Order.id
    ).filter(
        Order.job_type == JobType.retail,
        Invoice.invoice_date >= start_date,
        Invoice.invoice_date <= end_date,
    ).scalar() or 0.0

    fab_revenue_raw = revenue_invoiced - retail_revenue_raw

    # Revenue share pct
    retail_pct = round(retail_revenue_raw / revenue_invoiced * 100, 1) if revenue_invoiced else None
    fab_pct    = round(fab_revenue_raw    / revenue_invoiced * 100, 1) if revenue_invoiced else None

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
        "period_labels":    period_labels,
        "start_date":       start_date,
        "end_date":         end_date,
        "date_from":        date_from or "",
        "date_to":          date_to or "",
        # revenue
        "revenue_collected":  revenue_collected,
        "revenue_invoiced":   revenue_invoiced,
        "outstanding":        outstanding,
        "overdue":            overdue,
        # orders
        "orders_created":   orders_created,
        "orders_completed": orders_completed,
        "orders_cancelled": orders_cancelled,
        "orders_active":    orders_active,
        "avg_order_value":  avg_order_value,
        # quotes
        "q_total":          q_total,
        "q_sent":           q_sent,
        "q_converted":      q_converted,
        "q_declined":       q_declined,
        "q_expired":        q_expired,
        "conversion_rate":  conversion_rate,
        # tables
        "top_customers":    top_customers_raw,
        "labor_rows":       labor_rows,
        "dept_labels":      dept_labels,
        # retail vs fabrication
        "retail_order_count":  retail_order_count,
        "fab_order_count":     fab_order_count,
        "retail_revenue":      retail_revenue_raw,
        "fab_revenue":         fab_revenue_raw,
        "retail_pct":          retail_pct,
        "fab_pct":             fab_pct,
        # fulfillment
        "on_time_rate":              on_time_rate,
        "on_time_count":             on_time_count,
        "total_with_promise_count":  total_with_promise_count,
        "avg_cycle_days":            avg_cycle_days,
        "min_cycle_days":            min_cycle_days,
        "max_cycle_days":            max_cycle_days,
        "method_rows":               method_rows,
        "total_fulfilled":           total_fulfilled,
    })


# ── Helpers ───────────────────────────────────────────────────────────────
def _get_labor_cost_rates(db: Session) -> dict:
    rows = db.query(AppSetting).filter(AppSetting.key.like("labor_cost.%")).all()
    rates = {dept.value: 0.0 for dept in BillingDept}
    for row in rows:
        key = row.key.replace("labor_cost.", "")
        try:
            rates[key] = float(row.value or 0)
        except ValueError:
            pass
    return rates

DEPT_LABELS = {
    BillingDept.general_labor:       "General Labor",
    BillingDept.steel_fabrication:   "Steel Fabrication",
    BillingDept.aluminum_structural: "Aluminum / Structural",
}


# ── Margin & Estimating Report ────────────────────────────────────────────
@router.get("/margin", response_class=HTMLResponse)
async def margin_report(
    request: Request,
    sort_by: str = "margin_pct",
    sort_dir: str = "asc",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.role != UserRole.owner:
        return RedirectResponse("/reports", status_code=302)

    labor_cost_rates = _get_labor_cost_rates(db)

    # Load all invoiced/paid orders with everything we need
    orders = (
        db.query(Order)
        .options(
            joinedload(Order.customer),
            joinedload(Order.invoice),
            joinedload(Order.quote),
            joinedload(Order.line_items).joinedload(OrderLineItem.inventory_item),
            joinedload(Order.labor_entries).joinedload(LaborEntry.employee),
        )
        .filter(Order.status.in_([OrderStatus.invoiced, OrderStatus.paid]))
        .all()
    )

    rows = []
    for order in orders:
        inv = order.invoice
        if not inv or inv.payment_status.value == "void":
            continue

        revenue = inv.total or 0.0

        # Materials cost — only inventory-linked items have cost data
        mat_cost = 0.0
        has_partial_cost = False
        for li in order.line_items:
            if li.is_delivery_surcharge:
                continue
            if li.inventory_item and li.inventory_item.cost_per_unit:
                mat_cost += li.inventory_item.cost_per_unit * (li.quantity or 1)
            else:
                has_partial_cost = True  # non-linked item — cost unknown

        # Labor cost at each employee's individual cost rate
        labor_cost = sum(
            (e.hours or 0) * (e.employee.hourly_cost_rate or 0)
            for e in order.labor_entries
            if e.employee
        )

        total_cost = mat_cost + labor_cost
        gross_profit = revenue - total_cost
        margin_pct = round(gross_profit / revenue * 100, 1) if revenue > 0 else None

        # Estimate accuracy (requires linked quote with total_estimated)
        estimate = None
        variance = None
        variance_pct = None
        if order.quote and order.quote.total_estimated:
            estimate = order.quote.total_estimated
            variance = revenue - estimate
            variance_pct = round(variance / estimate * 100, 1) if estimate > 0 else None

        rows.append({
            "order":         order,
            "revenue":       revenue,
            "mat_cost":      mat_cost,
            "labor_cost":    labor_cost,
            "total_cost":    total_cost,
            "gross_profit":  gross_profit,
            "margin_pct":    margin_pct,
            "partial_cost":  has_partial_cost,
            "estimate":      estimate,
            "variance":      variance,
            "variance_pct":  variance_pct,
        })

    # Sort
    def _sort_key(r):
        if sort_by == "revenue":        return r["revenue"] or 0
        if sort_by == "gross_profit":   return r["gross_profit"] or 0
        if sort_by == "mat_cost":       return r["mat_cost"] or 0
        if sort_by == "labor_cost":     return r["labor_cost"] or 0
        if sort_by == "total_cost":     return r["total_cost"] or 0
        if sort_by == "estimate":       return r["estimate"] or 0
        if sort_by == "variance_pct":   return r["variance_pct"] or 0
        if sort_by == "customer":
            return (r["order"].customer.name if r["order"].customer else "")
        if sort_by == "order_number":
            return r["order"].order_number or ""
        return r["margin_pct"] if r["margin_pct"] is not None else -999

    rows.sort(key=_sort_key, reverse=(sort_dir == "desc"))

    # Summary stats (only rows with a margin_pct)
    margin_rows = [r for r in rows if r["margin_pct"] is not None]
    avg_margin   = round(sum(r["margin_pct"] for r in margin_rows) / len(margin_rows), 1) if margin_rows else None
    total_rev    = sum(r["revenue"] for r in rows)
    total_profit = sum(r["gross_profit"] for r in rows)
    total_cost   = sum(r["total_cost"] for r in rows)

    # Estimate accuracy summary
    est_rows     = [r for r in rows if r["variance_pct"] is not None]
    avg_variance = round(sum(r["variance_pct"] for r in est_rows) / len(est_rows), 1) if est_rows else None
    over_count   = sum(1 for r in est_rows if (r["variance"] or 0) > 0)
    under_count  = sum(1 for r in est_rows if (r["variance"] or 0) < 0)

    from app.models.user import User as UserModel
    rates_configured = db.query(UserModel).filter(
        UserModel.hourly_cost_rate != None,
        UserModel.hourly_cost_rate > 0,
    ).count() > 0

    return templates.TemplateResponse("reports/margin.html", {
        "request":           request,
        "user":              user,
        "can_see_financials": True,
        "orders":            order_rows,
        "sort_by":           sort_by,
        "sort_dir":          sort_dir,
        "rates_configured":  rates_configured,
    })
