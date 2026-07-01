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

# ── Root redirect ─────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return RedirectResponse("/dashboard")

# ── Dashboard ─────────────────────────────────────────────────────────────
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    from app.models.order import Order, OrderStatus
    from app.models.invoice import Invoice, PaymentStatus
    # Role-appropriate data fetch
    active_orders = db.query(Order).filter(
        Order.status.notin_([OrderStatus.paid, OrderStatus.cancelled])
    ).order_by(Order.created_at.desc()).limit(20).all()
    overdue_invoices = db.query(Invoice).filter(
        Invoice.payment_status == PaymentStatus.overdue
    ).count()
    return templates.TemplateResponse("dashboard/index.html", {
        "request": request,
        "user": user,
        "active_orders": active_orders,
        "overdue_invoices": overdue_invoices,
        "can_see_financials": financials_visible(user),
    })
