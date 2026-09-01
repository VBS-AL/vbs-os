from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from datetime import date, timedelta
from collections import defaultdict

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
from app.models.user import User as UserModel, UserRole
from app.models.inventory import InventoryItem

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
        "general_labor":       "General Labor",
        "steel_fabrication":   "Steel Fabrication",
        "aluminum_structural": "Aluminum / Structural",
        "fab":                 "Fabrication",
        "weld":                "Welding",
        "paint":               "Paint",
        "install":             "Installation",
        "other":               "Other",
    }

    # ── Monthly revenue trend (last 12 months) ────────────────────────────
    trend_start = date(today.year - 1, today.month, 1)
    monthly_raw = db.query(
        func.to_char(Invoice.invoice_date, 'YYYY-MM').label('month'),
        func.sum(Invoice.total).label('revenue'),
    ).filter(
        Invoice.invoice_date >= trend_start,
        Invoice.payment_status != PaymentStatus.void,
    ).group_by(func.to_char(Invoice.invoice_date, 'YYYY-MM')).all()

    monthly_map = {row.month: float(row.revenue or 0) for row in monthly_raw}
    monthly_trend = []
    for i in range(11, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        key   = f"{y:04d}-{m:02d}"
        label = date(y, m, 1).strftime('%b %y')
        monthly_trend.append({"key": key, "label": label, "revenue": monthly_map.get(key, 0.0)})
    trend_max = max((t["revenue"] for t in monthly_trend), default=1) or 1

    # ── AR aging breakdown ────────────────────────────────────────────────
    ar_open = db.query(Invoice).options(
        joinedload(Invoice.order).joinedload(Order.customer)
    ).filter(
        Invoice.balance_due > 0,
        Invoice.payment_status.notin_([PaymentStatus.paid, PaymentStatus.void]),
    ).all()

    ar_buckets = [
        {"label": "Current (0–30 days)",  "key": "current",  "invoices": [], "total": 0.0},
        {"label": "31–60 days",           "key": "days3160", "invoices": [], "total": 0.0},
        {"label": "61–90 days",           "key": "days6190", "invoices": [], "total": 0.0},
        {"label": "90+ days",             "key": "over90",   "invoices": [], "total": 0.0},
    ]
    for inv in ar_open:
        age = (today - inv.invoice_date).days
        if age <= 30:   b = ar_buckets[0]
        elif age <= 60: b = ar_buckets[1]
        elif age <= 90: b = ar_buckets[2]
        else:           b = ar_buckets[3]
        b["invoices"].append({"inv": inv, "age": age,
                              "customer": inv.order.customer.display_name if inv.order and inv.order.customer else "—"})
        b["total"] += inv.balance_due or 0.0
    ar_total = sum(b["total"] for b in ar_buckets)

    # ── Inventory value snapshot ──────────────────────────────────────────
    inventory_value = db.query(
        func.sum(InventoryItem.quantity_on_hand * InventoryItem.cost_per_unit)
    ).filter(
        InventoryItem.is_active == True,
        InventoryItem.cost_per_unit != None,
    ).scalar() or 0.0

    inventory_total_count  = db.query(InventoryItem).filter(InventoryItem.is_active == True).count()
    inventory_costed_count = db.query(InventoryItem).filter(
        InventoryItem.is_active == True, InventoryItem.cost_per_unit != None,
    ).count()
    inventory_low_stock = db.query(InventoryItem).filter(
        InventoryItem.is_active == True,
        InventoryItem.reorder_threshold != None,
        InventoryItem.quantity_on_hand <= InventoryItem.reorder_threshold,
    ).count()

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
        # monthly trend
        "monthly_trend":             monthly_trend,
        "trend_max":                 trend_max,
        # AR aging
        "ar_buckets":                ar_buckets,
        "ar_total":                  ar_total,
        # inventory
        "inventory_value":           inventory_value,
        "inventory_total_count":     inventory_total_count,
        "inventory_costed_count":    inventory_costed_count,
        "inventory_low_stock":       inventory_low_stock,
        # data quality
        "unlinked_items_count": db.query(OrderLineItem).join(Order).filter(
            OrderLineItem.inventory_item_id == None,
            OrderLineItem.is_delivery_surcharge == False,
            OrderLineItem.third_party_cost == None,
            Order.status.not_in([OrderStatus.cancelled, OrderStatus.paid]),
        ).count() if user.role.value in ["owner", "ops_manager"] else 0,
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

MARGIN_PERIOD_LABELS = {
    "all": "All Time",
    "ytd": "Year to Date",
    "qtd": "Quarter to Date",
    "mtd": "Month to Date",
    "90d": "Last 90 Days",
    "30d": "Last 30 Days",
}

JOB_TYPE_LABELS = {
    "fabrication": "Fabrication",
    "retail":      "Retail",
    "custom":      "Custom",
    "other":       "Other",
}


def _build_margin_rows(orders):
    """Convert a list of Order ORM objects into margin row dicts."""
    rows = []
    for order in orders:
        inv = order.invoice
        if not inv:
            continue

        revenue = inv.total or 0.0

        mat_cost = 0.0
        has_partial_cost = False
        for li in order.line_items:
            if getattr(li, "is_delivery_surcharge", False):
                continue
            if li.inventory_item and li.inventory_item.cost_per_unit:
                mat_cost += li.inventory_item.cost_per_unit * (li.quantity or 1)
            else:
                has_partial_cost = True

        labor_cost = sum(
            (e.hours or 0) * (e.employee.hourly_cost_rate or 0)
            for e in order.labor_entries
            if e.employee
        )

        total_cost   = mat_cost + labor_cost
        gross_profit = revenue - total_cost
        margin_pct   = round(gross_profit / revenue * 100, 1) if revenue > 0 else None

        estimate = variance = variance_pct = None
        if order.quote and order.quote.total_estimated:
            estimate    = order.quote.total_estimated
            variance    = revenue - estimate
            variance_pct = round(variance / estimate * 100, 1) if estimate > 0 else None

        rows.append({
            "order":         order,
            "invoice_date":  inv.invoice_date,
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
    return rows


def _load_margin_orders(db: Session, start_date: date, end_date: date):
    return (
        db.query(Order)
        .options(
            joinedload(Order.customer),
            joinedload(Order.invoice),
            joinedload(Order.quote),
            joinedload(Order.line_items).joinedload(OrderLineItem.inventory_item),
            joinedload(Order.labor_entries).joinedload(LaborEntry.employee),
        )
        .join(Invoice, Invoice.order_id == Order.id)
        .filter(
            Order.status.in_([OrderStatus.invoiced, OrderStatus.paid]),
            Invoice.invoice_date >= start_date,
            Invoice.invoice_date <= end_date,
            Invoice.payment_status != PaymentStatus.void,
        )
        .all()
    )


def _margin_period_dates(period: str, date_from: str, date_to: str):
    """Return (start_date, end_date, resolved_period, date_from_str, date_to_str)."""
    today = date.today()
    if date_from and date_to:
        try:
            return date.fromisoformat(date_from), date.fromisoformat(date_to), "custom", date_from, date_to
        except ValueError:
            pass
    if period == "all":
        return date(2000, 1, 1), today, "all", "", ""
    if period not in MARGIN_PERIOD_LABELS:
        period = "ytd"
    return get_start_date(period), today, period, "", ""


@router.get("/margin", response_class=HTMLResponse)
async def margin_report(
    request: Request,
    period: str = "ytd",
    date_from: str = None,
    date_to: str = None,
    sort_by: str = "margin_pct",
    sort_dir: str = "desc",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.role != UserRole.owner:
        return RedirectResponse("/reports", status_code=302)

    start_date, end_date, period, date_from, date_to = _margin_period_dates(
        period, date_from, date_to
    )

    period_labels = dict(MARGIN_PERIOD_LABELS)
    if period == "custom":
        period_labels["custom"] = f"{date_from} – {date_to}"

    orders = _load_margin_orders(db, start_date, end_date)
    rows   = _build_margin_rows(orders)

    # ── Sort ──────────────────────────────────────────────────────────────
    def _sort_key(r):
        if sort_by == "revenue":       return r["revenue"] or 0
        if sort_by == "gross_profit":  return r["gross_profit"] or 0
        if sort_by == "mat_cost":      return r["mat_cost"] or 0
        if sort_by == "labor_cost":    return r["labor_cost"] or 0
        if sort_by == "total_cost":    return r["total_cost"] or 0
        if sort_by == "estimate":      return r["estimate"] or 0
        if sort_by == "variance_pct":  return r["variance_pct"] or 0
        if sort_by == "invoice_date":  return r["invoice_date"] or date(2000, 1, 1)
        if sort_by == "customer":      return (r["order"].customer.display_name if r["order"].customer else "")
        if sort_by == "order_number":  return r["order"].order_number or ""
        return r["margin_pct"] if r["margin_pct"] is not None else -999

    rows.sort(key=_sort_key, reverse=(sort_dir == "desc"))

    # ── Summary stats ─────────────────────────────────────────────────────
    total_rev       = sum(r["revenue"]      for r in rows)
    total_profit    = sum(r["gross_profit"] for r in rows)
    total_cost      = sum(r["total_cost"]   for r in rows)
    total_mat       = sum(r["mat_cost"]     for r in rows)
    total_labor     = sum(r["labor_cost"]   for r in rows)
    has_any_partial = any(r["partial_cost"] for r in rows)
    avg_margin      = round(total_profit / total_rev * 100, 1) if total_rev > 0 else None

    est_rows     = [r for r in rows if r["variance_pct"] is not None]
    avg_variance = round(sum(r["variance_pct"] for r in est_rows) / len(est_rows), 1) if est_rows else None
    over_count   = sum(1 for r in est_rows if (r["variance"] or 0) > 0)
    under_count  = sum(1 for r in est_rows if (r["variance"] or 0) < 0)

    # ── Job type breakdown ────────────────────────────────────────────────
    from collections import defaultdict
    jt_stats: dict = defaultdict(lambda: {"count": 0, "revenue": 0.0, "profit": 0.0, "margins": []})
    for r in rows:
        jt = r["order"].job_type.value if r["order"].job_type else "other"
        jt_stats[jt]["count"]   += 1
        jt_stats[jt]["revenue"] += r["revenue"]
        jt_stats[jt]["profit"]  += r["gross_profit"]
        if r["margin_pct"] is not None:
            jt_stats[jt]["margins"].append(r["margin_pct"])

    job_breakdown = []
    for jt, stats in sorted(jt_stats.items()):
        avg_m = round(stats["profit"] / stats["revenue"] * 100, 1) if stats["revenue"] > 0 else None
        job_breakdown.append({
            "job_type":  jt,
            "label":     JOB_TYPE_LABELS.get(jt, jt.replace('_', ' ').title()),
            "count":     stats["count"],
            "revenue":   stats["revenue"],
            "profit":    stats["profit"],
            "avg_margin": avg_m,
        })

    from app.models.user import User as UserModel
    rates_configured = db.query(UserModel).filter(
        UserModel.hourly_cost_rate != None,
        UserModel.hourly_cost_rate > 0,
    ).count() > 0

    return templates.TemplateResponse("reports/margin.html", {
        "request":          request,
        "user":             user,
        "can_see_financials": True,
        "rows":             rows,
        "sort_by":          sort_by,
        "sort_dir":         sort_dir,
        "period":           period,
        "period_labels":    period_labels,
        "start_date":       start_date,
        "end_date":         end_date,
        "date_from":        date_from,
        "date_to":          date_to,
        "rates_configured": rates_configured,
        "avg_margin":       avg_margin,
        "total_rev":        total_rev,
        "total_profit":     total_profit,
        "total_cost":       total_cost,
        "total_mat":        total_mat,
        "total_labor":      total_labor,
        "has_any_partial":  has_any_partial,
        "avg_variance":     avg_variance,
        "over_count":       over_count,
        "under_count":      under_count,
        "est_rows_count":   len(est_rows),
        "job_breakdown":    job_breakdown,
    })


@router.get("/margin/csv")
async def margin_report_csv(
    request: Request,
    period: str = "ytd",
    date_from: str = None,
    date_to: str = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi.responses import StreamingResponse
    import csv, io

    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.role != UserRole.owner:
        return RedirectResponse("/reports", status_code=302)

    start_date, end_date, period, date_from, date_to = _margin_period_dates(
        period, date_from, date_to
    )
    orders = _load_margin_orders(db, start_date, end_date)
    rows   = _build_margin_rows(orders)
    rows.sort(key=lambda r: r["margin_pct"] if r["margin_pct"] is not None else -999, reverse=True)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Order", "Customer", "Job Type", "Invoice Date",
        "Revenue", "Mat Cost", "Labor Cost", "Total Cost",
        "Gross Profit", "Margin %",
        "Estimate", "Variance $", "Variance %",
    ])
    for r in rows:
        o = r["order"]
        writer.writerow([
            o.order_number,
            o.customer.display_name if o.customer else "",
            o.job_type.value if o.job_type else "",
            r["invoice_date"].isoformat() if r["invoice_date"] else "",
            f"{r['revenue']:.2f}",
            f"{r['mat_cost']:.2f}",
            f"{r['labor_cost']:.2f}",
            f"{r['total_cost']:.2f}",
            f"{r['gross_profit']:.2f}",
            f"{r['margin_pct']}" if r["margin_pct"] is not None else "",
            f"{r['estimate']:.2f}" if r["estimate"] is not None else "",
            f"{r['variance']:.2f}" if r["variance"] is not None else "",
            f"{r['variance_pct']}" if r["variance_pct"] is not None else "",
        ])

    buf.seek(0)
    filename = f"margin_{period}_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Production Analytics Report ───────────────────────────────────────────

PROD_PERIOD_LABELS = {
    "all": "All Time",
    "ytd": "Year to Date",
    "qtd": "Quarter to Date",
    "mtd": "Month to Date",
    "90d": "Last 90 Days",
    "30d": "Last 30 Days",
}

BILLING_RATE_MAP = {
    BillingDept.general_labor:       80.0,
    BillingDept.steel_fabrication:  100.0,
    BillingDept.aluminum_structural: 120.0,
    BillingDept.hot_walk_in:        150.0,
    BillingDept.welding_truck:      120.0,
}

DEPT_LABEL_MAP = {
    "general_labor":       "General Labor",
    "steel_fabrication":   "Steel Fabrication",
    "aluminum_structural": "Aluminum / Structural",
}

JOB_TYPE_LABEL_MAP = {
    "fabrication": "Fabrication",
    "structural":  "Structural",
    "beam":        "Beam",
    "retail":      "Retail",
    "walk_in":     "Walk-In",
}


def _prod_period_dates(period: str, date_from: str, date_to: str):
    today = date.today()
    if date_from and date_to:
        try:
            return date.fromisoformat(date_from), date.fromisoformat(date_to), "custom", date_from, date_to
        except ValueError:
            pass
    if period == "all":
        return date(2000, 1, 1), today, "all", "", ""
    if period not in PROD_PERIOD_LABELS:
        period = "mtd"
    return get_start_date(period), today, period, "", ""


@router.get("/production", response_class=HTMLResponse)
async def production_report(
    request: Request,
    period: str = "mtd",
    date_from: str = None,
    date_to: str = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.role not in [UserRole.owner, UserRole.ops_manager, UserRole.shop_foreman]:
        return RedirectResponse("/dashboard", status_code=302)

    start_date, end_date, period, date_from, date_to = _prod_period_dates(period, date_from, date_to)

    period_labels = dict(PROD_PERIOD_LABELS)
    if period == "custom":
        period_labels["custom"] = f"{date_from} – {date_to}"

    # ── Load completed orders in period (invoiced or paid) ────────────────
    completed_orders = (
        db.query(Order)
        .options(
            joinedload(Order.customer),
            joinedload(Order.invoice),
            joinedload(Order.line_items),
            joinedload(Order.labor_entries).joinedload(LaborEntry.employee),
            joinedload(Order.production_stages),
        )
        .join(Invoice, Invoice.order_id == Order.id)
        .filter(
            Order.status.in_([OrderStatus.invoiced, OrderStatus.paid]),
            Invoice.invoice_date >= start_date,
            Invoice.invoice_date <= end_date,
            Invoice.payment_status != PaymentStatus.void,
        )
        .all()
    )

    # ── Labor variance per order ──────────────────────────────────────────
    labor_rows = []
    for order in completed_orders:
        # Estimated from line items
        est_hours = 0.0
        est_cost  = 0.0
        for li in order.line_items:
            if li.estimated_labor_hours and li.estimated_labor_dept:
                rate = BILLING_RATE_MAP.get(BillingDept(li.estimated_labor_dept), 0.0) \
                       if li.estimated_labor_dept in [d.value for d in BillingDept] else 0.0
                est_hours += li.estimated_labor_hours
                est_cost  += li.estimated_labor_hours * rate

        # Actual from labor entries
        act_hours = sum(e.hours or 0.0 for e in order.labor_entries)
        act_cost  = sum(e.billed_value or 0.0 for e in order.labor_entries)

        var_hours = act_hours - est_hours
        var_cost  = act_cost  - est_cost
        var_pct   = round(var_cost / est_cost * 100, 1) if est_cost > 0 else None

        labor_rows.append({
            "order":      order,
            "est_hours":  round(est_hours, 2),
            "est_cost":   round(est_cost, 2),
            "act_hours":  round(act_hours, 2),
            "act_cost":   round(act_cost, 2),
            "var_hours":  round(var_hours, 2),
            "var_cost":   round(var_cost, 2),
            "var_pct":    var_pct,
            "rework_count": order.rework_count or 0,
        })

    # Sort by variance pct descending (biggest overruns first)
    labor_rows.sort(key=lambda r: r["var_pct"] if r["var_pct"] is not None else 0, reverse=True)

    # Aggregate labor stats
    agg_est_hours  = sum(r["est_hours"] for r in labor_rows)
    agg_est_cost   = sum(r["est_cost"]  for r in labor_rows)
    agg_act_hours  = sum(r["act_hours"] for r in labor_rows)
    agg_act_cost   = sum(r["act_cost"]  for r in labor_rows)
    agg_var_hours  = agg_act_hours - agg_est_hours
    agg_var_cost   = agg_act_cost  - agg_est_cost
    agg_var_pct    = round(agg_var_cost / agg_est_cost * 100, 1) if agg_est_cost > 0 else None
    over_budget    = sum(1 for r in labor_rows if (r["var_cost"] or 0) > 0)
    under_budget   = sum(1 for r in labor_rows if (r["var_cost"] or 0) < 0)

    # ── Rework stats ──────────────────────────────────────────────────────
    total_completed  = len(completed_orders)
    rework_orders    = [o for o in completed_orders if (o.rework_count or 0) > 0]
    rework_count     = len(rework_orders)
    rework_rate      = round(rework_count / total_completed * 100, 1) if total_completed > 0 else 0.0
    total_rework_cycles = sum(o.rework_count or 0 for o in completed_orders)

    # Rework by job type
    jt_rework: dict = defaultdict(lambda: {"total": 0, "rework": 0})
    for o in completed_orders:
        jt = o.job_type.value if o.job_type else "other"
        jt_rework[jt]["total"] += 1
        if (o.rework_count or 0) > 0:
            jt_rework[jt]["rework"] += 1
    rework_by_job_type = [
        {
            "job_type": jt,
            "label":    JOB_TYPE_LABEL_MAP.get(jt, jt.replace("_", " ").title()),
            "total":    stats["total"],
            "rework":   stats["rework"],
            "rate":     round(stats["rework"] / stats["total"] * 100, 1) if stats["total"] else 0.0,
        }
        for jt, stats in sorted(jt_rework.items(), key=lambda x: x[1]["rework"], reverse=True)
    ]

    # Top customers by rework job count
    cust_rework: dict = defaultdict(lambda: {"name": "", "total": 0, "rework": 0})
    for o in completed_orders:
        cname = o.customer.display_name if o.customer else "Unknown"
        cust_rework[cname]["name"]  = cname
        cust_rework[cname]["total"] += 1
        if (o.rework_count or 0) > 0:
            cust_rework[cname]["rework"] += 1
    top_rework_customers = sorted(
        [v for v in cust_rework.values() if v["rework"] > 0],
        key=lambda x: x["rework"], reverse=True
    )[:8]

    # ── Employee productivity ─────────────────────────────────────────────
    labor_entries_in_period = (
        db.query(LaborEntry)
        .options(joinedload(LaborEntry.employee))
        .filter(
            LaborEntry.work_date >= start_date,
            LaborEntry.work_date <= end_date,
        )
        .all()
    )

    emp_stats: dict = defaultdict(lambda: {
        "employee": None, "hours": 0.0, "billed_value": 0.0,
        "orders": set(), "dept_hours": defaultdict(float),
    })
    for e in labor_entries_in_period:
        key = e.employee_id or 0
        emp_stats[key]["employee"]     = e.employee
        emp_stats[key]["hours"]       += e.hours or 0.0
        emp_stats[key]["billed_value"] += e.billed_value or 0.0
        if e.order_id:
            emp_stats[key]["orders"].add(e.order_id)
        if e.billing_dept:
            dept_val = e.billing_dept.value if hasattr(e.billing_dept, "value") else str(e.billing_dept)
            emp_stats[key]["dept_hours"][dept_val] += e.hours or 0.0

    employee_rows = sorted(
        [
            {
                "employee":    v["employee"],
                "hours":       round(v["hours"], 1),
                "billed_value": round(v["billed_value"], 2),
                "order_count": len(v["orders"]),
                "dept_hours":  dict(v["dept_hours"]),
            }
            for v in emp_stats.values()
            if v["employee"] is not None
        ],
        key=lambda x: x["hours"], reverse=True,
    )

    # Dept breakdown
    dept_stats: dict = defaultdict(lambda: {"hours": 0.0, "billed_value": 0.0})
    for e in labor_entries_in_period:
        dept_val = e.billing_dept.value if hasattr(e.billing_dept, "value") else str(e.billing_dept)
        dept_stats[dept_val]["hours"]       += e.hours or 0.0
        dept_stats[dept_val]["billed_value"] += e.billed_value or 0.0
    dept_rows = sorted(
        [
            {
                "dept":        k,
                "label":       DEPT_LABEL_MAP.get(k, k.replace("_", " ").title()),
                "hours":       round(v["hours"], 1),
                "billed_value": round(v["billed_value"], 2),
            }
            for k, v in dept_stats.items()
        ],
        key=lambda x: x["hours"], reverse=True,
    )
    total_prod_hours  = round(sum(r["hours"]        for r in dept_rows), 1)
    total_billed_val  = round(sum(r["billed_value"] for r in dept_rows), 2)

    return templates.TemplateResponse("reports/production.html", {
        "request":        request,
        "user":           user,
        "period":         period,
        "period_labels":  period_labels,
        "start_date":     start_date,
        "end_date":       end_date,
        "date_from":      date_from,
        "date_to":        date_to,
        # labor variance
        "labor_rows":         labor_rows,
        "agg_est_hours":      round(agg_est_hours, 1),
        "agg_est_cost":       round(agg_est_cost, 2),
        "agg_act_hours":      round(agg_act_hours, 1),
        "agg_act_cost":       round(agg_act_cost, 2),
        "agg_var_hours":      round(agg_var_hours, 1),
        "agg_var_cost":       round(agg_var_cost, 2),
        "agg_var_pct":        agg_var_pct,
        "over_budget":        over_budget,
        "under_budget":       under_budget,
        # rework
        "total_completed":        total_completed,
        "rework_count":           rework_count,
        "rework_rate":            rework_rate,
        "total_rework_cycles":    total_rework_cycles,
        "rework_by_job_type":     rework_by_job_type,
        "top_rework_customers":   top_rework_customers,
        # employee productivity
        "employee_rows":      employee_rows,
        "dept_rows":          dept_rows,
        "total_prod_hours":   total_prod_hours,
        "total_billed_val":   total_billed_val,
        "dept_label_map":     DEPT_LABEL_MAP,
    })


@router.get("/unlinked-items", response_class=HTMLResponse)
async def unlinked_items_audit(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user or user.role.value not in ["owner", "ops_manager"]:
        return RedirectResponse("/reports", status_code=302)

    # Active orders (not cancelled/paid) with at least one unlinked, non-system line item
    unlinked = (
        db.query(OrderLineItem)
        .join(Order, OrderLineItem.order_id == Order.id)
        .options(
            joinedload(OrderLineItem.order).joinedload(Order.customer),
        )
        .filter(
            OrderLineItem.inventory_item_id == None,
            OrderLineItem.is_delivery_surcharge == False,
            OrderLineItem.third_party_cost == None,
            Order.status.not_in([OrderStatus.cancelled, OrderStatus.paid]),
        )
        .order_by(Order.id.desc())
        .all()
    )

    # Group by order
    from collections import defaultdict
    by_order: dict = defaultdict(list)
    for li in unlinked:
        by_order[li.order_id].append(li)

    orders_with_unlinked = []
    for order_id, items in by_order.items():
        orders_with_unlinked.append({
            "order":  items[0].order,
            "items":  items,
        })

    return templates.TemplateResponse("reports/unlinked_items.html", {
        "request":              request,
        "user":                 user,
        "orders_with_unlinked": orders_with_unlinked,
        "total_count":          len(unlinked),
    })
