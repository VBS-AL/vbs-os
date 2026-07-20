from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from datetime import date, timedelta

from app.database import get_db
from app.auth import require_user, require_management, financials_visible
from app.models.user import User, UserRole
from app.models.order import Order, OrderStatus
from app.models.invoice import Invoice, Payment, PaymentStatus, PaymentMethod
from app.models.labor import LaborEntry, BILLING_RATES

router = APIRouter(prefix="/invoices", tags=["invoices"])
templates = Jinja2Templates(directory="app/templates")


def _load_invoice(invoice_id: int, db: Session) -> Invoice:
    inv = db.query(Invoice).options(
        joinedload(Invoice.order).joinedload(Order.customer),
        joinedload(Invoice.order).joinedload(Order.line_items),
        joinedload(Invoice.order).joinedload(Order.labor_entries),
        joinedload(Invoice.payments),
    ).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


# ── Invoice List ──────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
async def invoice_list(
    request: Request,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if user.role.value not in ["owner", "ops_manager"]:
        raise HTTPException(status_code=403, detail="Access denied")
    query = db.query(Invoice).options(
        joinedload(Invoice.order).joinedload(Order.customer)
    )
    if status:
        query = query.filter(Invoice.payment_status == status)

    sort_map = {
        "invoice_number": Invoice.invoice_number,
        "status": Invoice.payment_status,
        "due_date": Invoice.due_date,
        "issued": Invoice.invoice_date,
    }
    if sort_by and sort_by in sort_map:
        col = sort_map[sort_by]
        query = query.order_by(col.desc() if sort_dir == "desc" else col.asc())
    else:
        query = query.order_by(Invoice.invoice_date.desc())

    invoices = query.all()

    # Python-side sorts for computed/joined fields
    if sort_by == "customer":
        invoices.sort(
            key=lambda i: (i.order.customer.name if i.order and i.order.customer else ""),
            reverse=(sort_dir == "desc"),
        )
    elif sort_by == "total":
        invoices.sort(key=lambda i: (i.total or 0), reverse=(sort_dir == "desc"))
    elif sort_by == "balance":
        invoices.sort(key=lambda i: (i.balance_due or 0), reverse=(sort_dir == "desc"))

    return templates.TemplateResponse("invoices/list.html", {
        "request": request, "user": user, "invoices": invoices,
        "statuses": PaymentStatus, "filter_status": status,
        "today": date.today(),
        "sort_by": sort_by, "sort_dir": sort_dir,
        "can_see_financials": financials_visible(user),
    })


# ── Generate Invoice from Order ───────────────────────────────────────────
@router.post("/generate/{order_id}")
async def generate_invoice(
    order_id: int,
    tax_rate: float = Form(0.0),
    due_days: int = Form(30),
    notes: str = Form(""),
    user: User = Depends(require_management),
    db: Session = Depends(get_db),
):
    order = db.query(Order).options(
        joinedload(Order.line_items),
        joinedload(Order.labor_entries),
        joinedload(Order.quote),
    ).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(404, "Order not found")

    if order.invoice:
        return RedirectResponse(f"/invoices/{order.invoice.id}", status_code=302)

    # Labor rates (must match invoice template)
    _LABOR_RATES = {'general_labor': 80, 'steel_fabrication': 100, 'aluminum_structural': 120}

    # Materials and delivery
    material_total  = sum((li.unit_price or 0) * li.quantity for li in order.line_items if not li.is_delivery_surcharge)
    delivery_total  = sum((li.unit_price or 0) * li.quantity for li in order.line_items if li.is_delivery_surcharge)

    # Labor: use whichever is higher — actual or estimated
    estimated_labor = sum(
        (li.estimated_labor_hours or 0) * _LABOR_RATES.get(li.estimated_labor_dept or '', 0)
        for li in order.line_items if not li.is_delivery_surcharge
    )
    actual_labor    = sum(e.billed_value for e in order.labor_entries)
    billed_labor    = max(estimated_labor, actual_labor)

    subtotal = material_total + billed_labor + delivery_total
    tax = round(subtotal * (tax_rate / 100), 2)
    total = round(subtotal + tax, 2)

    today = date.today()
    inv = Invoice(
        invoice_number=order.order_number,
        order_id=order.id,
        invoice_date=today,
        due_date=today + timedelta(days=due_days),
        payment_status=PaymentStatus.unpaid,
        subtotal=round(subtotal, 2),
        tax=tax,
        total=round(total, 2),
        amount_paid=0,
        balance_due=round(total, 2),
        notes=notes.strip() or None,
        created_by_id=user.id,
    )
    db.add(inv)

    # Move order to invoiced
    order.status = OrderStatus.invoiced
    db.commit()

    return RedirectResponse(f"/invoices/{inv.id}", status_code=302)


# ── Invoice Detail ────────────────────────────────────────────────────────
@router.get("/{invoice_id}", response_class=HTMLResponse)
async def invoice_detail(
    request: Request,
    invoice_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if user.role.value not in ["owner", "ops_manager"]:
        raise HTTPException(status_code=403, detail="Access denied")
    inv = _load_invoice(invoice_id, db)
    return templates.TemplateResponse("invoices/detail.html", {
        "request": request, "user": user, "inv": inv,
        "payment_methods": PaymentMethod,
        "today": date.today().isoformat(),
        "can_see_financials": financials_visible(user),
    })


# ── Record Payment ────────────────────────────────────────────────────────
@router.post("/{invoice_id}/payment")
async def record_payment(
    invoice_id: int,
    amount: float = Form(...),
    method: str = Form(...),
    payment_date: str = Form(...),
    reference: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_management),
    db: Session = Depends(get_db),
):
    inv = db.query(Invoice).options(joinedload(Invoice.order)).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404)

    payment = Payment(
        invoice_id=invoice_id,
        amount=round(amount, 2),
        method=PaymentMethod(method),
        payment_date=date.fromisoformat(payment_date),
        reference=reference.strip() or None,
        notes=notes.strip() or None,
        recorded_by_id=user.id,
    )
    db.add(payment)

    # Update invoice totals
    inv.amount_paid = round((inv.amount_paid or 0) + amount, 2)
    inv.balance_due = round(inv.total - inv.amount_paid, 2)

    if inv.balance_due <= 0:
        inv.payment_status = PaymentStatus.paid
        inv.balance_due = 0
        if inv.order:
            inv.order.status = OrderStatus.paid
    elif inv.amount_paid > 0:
        inv.payment_status = PaymentStatus.partial

    db.commit()
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=302)


# ── Print View ───────────────────────────────────────────────────────────
@router.get("/{invoice_id}/print", response_class=HTMLResponse)
async def invoice_print(
    request: Request,
    invoice_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if user.role.value not in ["owner", "ops_manager"]:
        raise HTTPException(status_code=403, detail="Access denied")
    inv = _load_invoice(invoice_id, db)
    return templates.TemplateResponse("invoices/print.html", {
        "request": request, "user": user, "inv": inv,
        "can_see_financials": financials_visible(user),
    })


# ── Void Invoice ──────────────────────────────────────────────────────────
@router.post("/{invoice_id}/void")
async def void_invoice(
    invoice_id: int,
    user: User = Depends(require_management),
    db: Session = Depends(get_db),
):
    inv = db.query(Invoice).options(joinedload(Invoice.order)).filter(Invoice.id == invoice_id).first