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
    from app.models.invoice import Invoice, Payment, PaymentStatus
    from app.models.quote import Quote, QuoteStatus

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
        Order.status.notin_([OrderStatus.delivered, OrderStatus.paid, OrderStatus.cancelled])
    ).order_by(Order.created_at.desc()).all()

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

    _LABOR_RATES = {'general_labor': 80, 'steel_fabrication': 100, 'aluminum_structural': 120}

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

    if can_fin:
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
        "in_production": sum(1 for o in active_orders if