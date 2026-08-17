from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager

from app.database import Base, engine, get_db
from app.models import *   # registers all models
from app.auth import get_current_user, financials_visible
from app.routers import auth as auth_router
from app.routers import orders as orders_router
from app.routers import customers as customers_router
from app.routers import users as users_router
from app.routers import invoices as invoices_router
from app.routers import quotes as quotes_router
from app.routers import inventory as inventory_router
from app.routers import reports as reports_router
from app.routers import production as production_router
from app.routers import packing as packing_router
from app.routers import drawings as drawings_router
from app.routers import fulfillment as fulfillment_router
from app.routers import maintenance as maintenance_router

# ── Number sequencing helpers ─────────────────────────────────────────────
import re
from datetime import date

def next_number(db: Session, model, field: str, prefix: str) -> str:
    """Generate next VBS number: e.g. VBS-O-26-00001"""
    yy = str(date.today().year)[2:]
    pattern = f"{prefix}-{yy}-"
    col = getattr(model, field)
    last = db.query(col).filter(col.like(f"{pattern}%")).order_by(col.desc()).first()
    if last:
        num = int(last[0].split("-")[-1]) + 1
    else:
        num = 1
    return f"{pattern}{num:05d}"

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _seed_admin(next(get_db()))
    yield

def _seed_admin(db: Session):
    """Create default owner account on first run."""
    from app.models.user import User, UserRole
    from app.auth import hash_password
    if db.query(User).count() == 0:
        admin = User(
            employee_id="EMP-001",
            first_name="David",
            last_name="Costa",
            email="admin@vanburen.local",
            hashed_password=hash_password("vbs-change-me"),
            role=UserRole.owner,
            is_active=True,
        )
        db.add(admin)
        db.commit()

app = FastAPI(title="Van Buren Steel OS", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# ── Routers ───────────────────────────────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(orders_router.router)
app.include_router(customers_router.router)
app.include_router(users_router.router)
app.include_router(invoices_router.router)
app.include_router(quotes_router.router)
app.include_router(inventory_router.router)
app.include_router(reports_router.router)
app.include_router(production_router.router)
app.include_router(packing_router.router)
app.include_router(drawings_router.router)
app.include_router(fulfillment_router.router)
app.include_router(maintenance_router.router)

# ── Root redirect ─────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return RedirectResponse("/dashboard")

# ── Dashboard ─────────────────────────────────────────────────────────────
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    period: str = "mtd",
    date_from: str = None,
    date_to: str = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)

    from datetime import date, timedelta
    from sqlalchemy import func
    from app.models.order import Order, OrderStatus
    from app.models.packing_list import PackingList as PL_model
    from sqlalchemy import not_, exists, or_ as sa_or
    from app.models.invoice import Invoice, Payment, PaymentStatus
    from app.models.quote import Quote, QuoteStatus
    from app.models.inventory import InventoryItem

    # ── Period start date ──────────────────────────────────────────────────
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
            start_date = today.replace(day=1)
    elif period == "qtd":
        q_month = ((today.month - 1) // 3) * 3 + 1
        start_date = today.replace(month=q_month, day=1)
    elif period == "ytd":
        start_date = today.replace(month=1, day=1)
    elif period == "30d":
        start_date = today - timedelta(days=30)
    elif period == "90d":
        start_date = today - timedelta(days=90)
    else:  # default: mtd
        period = "mtd"
        start_date = today.replace(day=1)

    period_labels = {
        "mtd": "Month to Date",
        "qtd": "Quarter to Date",
        "ytd": "Year to Date",
        "30d": "Last 30 Days",
        "90d": "Last 90 Days",
    }
    if period == "custom":
        period_labels["custom"] = f"{date_from} – {date_to}"

    # ── Live stats (no period filter) ─────────────────────────────────────
    from sqlalchemy.orm import joinedload as jl
    active_orders = db.query(Order).options(
        jl(Order.line_items), jl(Order.labor_entries)
    ).filter(
        Order.status.notin_([OrderStatus.invoiced, OrderStatus.delivered, OrderStatus.paid, OrderStatus.cancelled])
    ).order_by(Order.created_at.desc()).all()

    # ── Fulfillment pipeline counts (live) ────────────────────────────────
    awaiting_pl_count = db.query(Order).filter(
        Order.status == OrderStatus.ready,
        ~exists().where(PL_model.order_id == Order.id),
    ).count()

    awaiting_check_count = db.query(PL_model).join(PL_model.order).filter(
        Order.status == OrderStatus.ready,
        PL_model.checker_id != None,
        PL_model.check_confirmed == False,
    ).count()

    ready_to_fulfill_count = db.query(PL_model).join(PL_model.order).filter(
        Order.status == OrderStatus.ready,
        sa_or(PL_model.checker_id == None, PL_model.check_confirmed == True),
    ).count()

    fulfilled_today = db.query(PL_model).join(PL_model.order).filter(
        Order.status.in_([OrderStatus.delivered, OrderStatus.paid]),
        PL_model.date_shipped == today,
    ).count()

    pl_awaiting_check_alert = db.query(PL_model).join(PL_model.order).filter(
        PL_model.checker_id != None,
        PL_model.check_confirmed == False,
        Order.status == OrderStatus.ready,
    ).count()

    on_hold_count   = sum(1 for o in active_orders if o.status == OrderStatus.on_hold)
    ready_count     = sum(1 for o in active_orders if o.status == OrderStatus.ready)
    overdue_count   = db.query(Invoice).filter(Invoice.payment_status == PaymentStatus.overdue).count()

    # ── Period stats (financial — role-gated in template) ─────────────────
    can_fin = financials_visible(user)
    revenue_collected = 0.0
    revenue_invoiced  = 0.0
    outstanding       = 0.0
    orders_created    = 0
    jobs_completed    = 0
    quotes_sent       = 0
    quotes_converted  = 0

    _LABOR_RATES = {'general_labor': 80, 'steel_fabrication': 100, 'aluminum_structural': 120, 'hot_walk_in': 150, 'welding_truck': 120}

    def _order_est_total(o):
        """Use actual labor if logged, otherwise fall back to estimated labor."""
        mat = sum((li.unit_price or 0) * li.quantity for li in o.line_items)
        actual_labor = sum(le.billed_value or 0 for le in o.labor_entries)
        if actual_labor > 0:
            return mat + actual_labor
        est_labor = sum(
            (li.estimated_labor_hours or 0) * _LABOR_RATES.get(li.estimated_labor_dept or '', 0)
            for li in o.line_items
        )
        return mat + est_labor

    wip_backlog = 0.0
    wip_in_shop = 0.0
    active_total = 0.0
    on_hold_value = 0.0
    ready_value = 0.0
    overdue_amount = 0.0
    outstanding_count = 0
    invoiced_count = 0
    payments_count = 0

    if can_fin:
        # Combined invoice period query — count + sum in one pass to stay consistent
        _inv_row = db.query(
            func.count(Invoice.id),
            func.sum(Invoice.total),
        ).filter(
            Invoice.invoice_date >= start_date,
            Invoice.invoice_date <= end_date,
        ).first()
        invoiced_count   = _inv_row[0] or 0
        revenue_invoiced = _inv_row[1] or 0.0

        # Combined payment period query
        _pay_row = db.query(
            func.count(Payment.id),
            func.sum(Payment.amount),
        ).filter(
            Payment.payment_date >= start_date,
            Payment.payment_date <= end_date,
        ).first()
        payments_count    = _pay_row[0] or 0
        revenue_collected = _pay_row[1] or 0.0

        outstanding = db.query(func.sum(Invoice.balance_due)).filter(
            Invoice.payment_status.notin_([PaymentStatus.paid, PaymentStatus.void])
        ).scalar() or 0.0

        outstanding_count = db.query(func.count(Invoice.id)).filter(
            Invoice.payment_status.notin_([PaymentStatus.paid, PaymentStatus.void])
        ).scalar() or 0

        def _order_mat_total(o):
            return sum((li.unit_price or 0) * li.quantity for li in o.line_items)

        wip_backlog = sum(
            _order_est_total(o) for o in active_orders
            if o.status == OrderStatus.confirmed
        )
        wip_backlog_mat = sum(
            _order_mat_total(o) for o in active_orders
            if o.status == OrderStatus.confirmed
        )

        wip_in_shop = sum(
            _order_est_total(o) for o in active_orders
            if o.status.value in ('in_production', 'on_hold', 'qa_review', 'ready')
        )
        wip_in_shop_mat = sum(
            _order_mat_total(o) for o in active_orders
            if o.status.value in ('in_production', 'on_hold', 'qa_review', 'ready')
        )

        active_total  = sum(_order_est_total(o) for o in active_orders)
        on_hold_value = sum(_order_est_total(o) for o in active_orders if o.status == OrderStatus.on_hold)
        ready_value   = sum(_order_est_total(o) for o in active_orders if o.status == OrderStatus.ready)
        overdue_amount = db.query(func.sum(Invoice.balance_due)).filter(
            Invoice.payment_status == PaymentStatus.overdue
        ).scalar() or 0.0

    # ── Pipeline breakdown (from active_orders, no extra query) ──────────────
    pipeline = {
        "confirmed":     sum(1 for o in active_orders if o.status.value == "confirmed"),
        "in_production": sum(1 for o in active_orders if o.status.value == "in_production"),
        "qa_review":     sum(1 for o in active_orders if o.status.value == "qa_review"),
        "ready":         sum(1 for o in active_orders if o.status.value == "ready"),
    }

    # ── Overdue orders (promised_date past, still active) ─────────────────
    overdue_orders = [
        o for o in active_orders
        if o.promised_date and o.promised_date < today
    ]

    orders_created = db.query(Order).filter(
        func.date(Order.created_at) >= start_date,
        func.date(Order.created_at) <= end_date,
    ).count()

    jobs_completed = db.query(Order).filter(
        Order.status.in_([OrderStatus.delivered, OrderStatus.paid]),
        func.date(Order.created_at) >= start_date,
        func.date(Order.created_at) <= end_date,
    ).count()

    quotes_sent = db.query(Quote).filter(
        Quote.status.notin_([QuoteStatus.draft]),
        func.date(Quote.created_at) >= start_date,
        func.date(Quote.created_at) <= end_date,
    ).count()

    quotes_converted = db.query(Quote).filter(
        Quote.status == QuoteStatus.converted,
        func.date(Quote.created_at) >= start_date,
        func.date(Quote.created_at) <= end_date,
    ).count()

    # ── Quote pipeline (live — for pipeline section) ───────────────────────
    from app.models.quote import QuoteLineItem
    from sqlalchemy.orm import joinedload as jl2

    pipeline_quotes_draft = db.query(Quote).options(
        jl2(Quote.customer), jl2(Quote.line_items)
    ).filter(Quote.status == QuoteStatus.draft).order_by(Quote.created_at.desc()).all()

    pipeline_quotes_sent = db.query(Quote).options(
        jl2(Quote.customer), jl2(Quote.line_items)
    ).filter(Quote.status == QuoteStatus.sent).order_by(Quote.created_at.desc()).all()

    def _quote_total(q):
        lr = {'general_labor': 80, 'steel_fabrication': 100, 'aluminum_structural': 120, 'hot_walk_in': 150, 'welding_truck': 120}
        total = 0.0
        for li in q.line_items:
            total += (li.unit_price or 0) * li.quantity
            if li.estimated_labor_hours and li.estimated_labor_dept:
                total += li.estimated_labor_hours * lr.get(li.estimated_labor_dept, 0)
        return total

    draft_value = sum(_quote_total(q) for q in pipeline_quotes_draft) if can_fin else 0
    sent_value  = sum(_quote_total(q) for q in pipeline_quotes_sent)  if can_fin else 0

    # ── Low stock alert ───────────────────────────────────────────────────
    low_stock_count = db.query(InventoryItem).filter(
        InventoryItem.is_active == True,
        InventoryItem.reorder_threshold != None,
        InventoryItem.quantity_on_hand <= InventoryItem.reorder_threshold,
    ).count()

    # ── AR aging signal (30+ days) ────────────────────────────────────────
    ar_aging_cutoff = today - timedelta(days=30)
    ar_aging_30_count = db.query(Invoice).filter(
        Invoice.balance_due > 0,
        Invoice.payment_status.notin_([PaymentStatus.paid, PaymentStatus.void]),
        Invoice.invoice_date <= ar_aging_cutoff,
    ).count() if can_fin else 0
    ar_aging_30_amount = db.query(func.sum(Invoice.balance_due)).filter(
        Invoice.balance_due > 0,
        Invoice.payment_status.notin_([PaymentStatus.paid, PaymentStatus.void]),
        Invoice.invoice_date <= ar_aging_cutoff,
    ).scalar() or 0.0 if can_fin else 0.0

    return templates.TemplateResponse("dashboard/index.html", {
        "request":          request,
        "user":             user,
        "can_see_financials": can_fin,
        # period
        "period":           period,
        "period_label":     period_labels[period],
        "period_labels":    period_labels,
        "date_from":        date_from or "",
        "date_to":          date_to or "",
        # live
        "active_orders":    active_orders,
        "on_hold_count":    on_hold_count,
        "ready_count":      ready_count,
        "overdue_count":    overdue_count,
        # period metrics
        "revenue_collected":  revenue_collected,
        "revenue_invoiced":   revenue_invoiced,
        "outstanding":        outstanding,
        "outstanding_count":  outstanding_count,
        "invoiced_count":     invoiced_count,
        "payments_count":     payments_count,
        "orders_created":     orders_created,
        "jobs_completed":     jobs_completed,
        "quotes_sent":        quotes_sent,
        "quotes_converted":   quotes_converted,
        "pipeline":           pipeline,
        "overdue_orders":     overdue_orders,
        "today":              today,
        "pipeline_quotes_draft": pipeline_quotes_draft,
        "pipeline_quotes_sent":  pipeline_quotes_sent,
        "draft_value":           draft_value,
        "sent_value":            sent_value,
        "wip_backlog":        wip_backlog,
        "wip_backlog_mat":    wip_backlog_mat if can_fin else 0,
        "wip_in_shop":        wip_in_shop,
        "wip_in_shop_mat":    wip_in_shop_mat if can_fin else 0,
        "active_total":       active_total,
        "on_hold_value":      on_hold_value,
        "ready_value":        ready_value,
        "overdue_amount":     overdue_amount,
        "awaiting_pl_count":        awaiting_pl_count,
        "awaiting_check_count":     awaiting_check_count,
        "ready_to_fulfill_count":   ready_to_fulfill_count,
        "fulfilled_today":          fulfilled_today,
        "pl_awaiting_check_alert":  pl_awaiting_check_alert,
        "low_stock_count":          low_stock_count,
        "ar_aging_30_count":        ar_aging_30_count,
        "ar_aging_30_amount":       ar_aging_30_amount,
    })
