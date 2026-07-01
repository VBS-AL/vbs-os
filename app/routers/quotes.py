import json
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import Optional
from datetime import date, timedelta, datetime, timezone

from app.database import get_db
from app.auth import require_user, require_management, financials_visible
from app.models.user import User, UserRole
from app.models.quote import Quote, QuoteLineItem, QuoteStatus, QuoteRevision
from app.models.order import Order, OrderLineItem, OrderStatus, JobType, Priority
from app.models.customer import Customer
from app.models.production import ProductionStage, StageType

router = APIRouter(prefix="/quotes", tags=["quotes"])
templates = Jinja2Templates(directory="app/templates")

PAINT_OPTIONS = ["Galvanized","Yellow Oxide","Red Oxide","Black","Grey","Powder Coat","No Paint"]

def next_quote_number(db: Session) -> str:
    yy = str(date.today().year)[2:]
    prefix = f"VBS-Q-{yy}-"
    last = db.query(Quote.quote_number).filter(
        Quote.quote_number.like(f"{prefix}%")
    ).order_by(Quote.quote_number.desc()).first()
    num = (int(last[0].split("-")[-1]) + 1) if last else 1
    return f"{prefix}{num:05d}"

def check_expiry(quote: Quote) -> Quote:
    """Mark quote expired if past valid_until and still sent/accepted."""
    if (quote.status in [QuoteStatus.sent] and
            quote.valid_until and quote.valid_until < date.today()):
        quote.status = QuoteStatus.expired
    return quote

# ── Quote List ────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
async def quote_list(
    request: Request,
    status: Optional[str] = None,
    q: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = db.query(Quote).options(joinedload(Quote.customer))
    if status:
        query = query.filter(Quote.status == status)
    if q:
        query = query.join(Customer).filter(
            or_(Quote.quote_number.ilike(f"%{q}%"),
                Customer.name.ilike(f"%{q}%"))
        )

    sort_map = {
        "quote_number": Quote.quote_number,
        "status": Quote.status,
        "job_type": Quote.job_type,
        "valid_until": Quote.valid_until,
        "created": Quote.created_at,
    }
    if sort_by and sort_by in sort_map:
        col = sort_map[sort_by]
        query = query.order_by(col.desc() if sort_dir == "desc" else col.asc())
    else:
        query = query.order_by(Quote.created_at.desc())

    quotes = query.all()

    # Check expiry inline
    for qt in quotes:
        if check_expiry(qt).status == QuoteStatus.expired and qt.status != QuoteStatus.expired:
            db.commit()

    # Python-side sorts for computed/joined fields
    if sort_by == "customer":
        quotes.sort(
            key=lambda qt: (qt.customer.name if qt.customer else ""),
            reverse=(sort_dir == "desc"),
        )
    elif sort_by == "total":
        quotes.sort(key=lambda qt: (qt.total_estimated or 0), reverse=(sort_dir == "desc"))

    return templates.TemplateResponse("quotes/list.html", {
        "request": request, "user": user, "quotes": quotes,
        "statuses": QuoteStatus, "filter_status": status, "q": q,
        "today": date.today(),
        "sort_by": sort_by, "sort_dir": sort_dir,
        "can_see_financials": financials_visible(user),
    })

# ── New Quote ─────────────────────────────────────────────────────────────
@router.get("/new", response_class=HTMLResponse)
async def new_quote_form(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    customers = db.query(Customer).filter(Customer.is_active == True).order_by(Customer.name).all()
    return templates.TemplateResponse("quotes/new.html", {
        "request": request, "user": user,
        "customers": customers,
        "job_types": JobType, "priorities": Priority,
        "paint_options": PAINT_OPTIONS,
        "today": date.today().isoformat(),
        "can_see_financials": financials_visible(user),
    })

@router.post("/new")
async def create_quote(
    request: Request,
    customer_id: Optional[int] = Form(None),
    job_type: str = Form(...),
    priority: str = Form("standard"),
    paint_spec: str = Form(""),
    drawings_required: bool = Form(False),
    description: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    customers = db.query(Customer).filter(Customer.is_active == True).order_by(Customer.name).all()

    errors = []
    if not customer_id:
        errors.append("Please select a customer.")
    if not paint_spec or not paint_spec.strip():
        errors.append("Paint Spec is required.")
    if not description or not description.strip():
        errors.append("Description / Scope is required.")
    if errors:
        return templates.TemplateResponse("quotes/new.html", {
            "request": request, "user": user, "customers": customers,
            "job_types": JobType, "priorities": Priority,
            "paint_options": PAINT_OPTIONS,
            "today": date.today().isoformat(),
            "error": " ".join(errors),
            "can_see_financials": financials_visible(user),
        }, status_code=422)

    quote_num = next_quote_number(db)
    quote = Quote(
        quote_number=quote_num,
        customer_id=customer_id,
        job_type=job_type,
        priority=priority,
        paint_spec=paint_spec or None,
        drawings_required=drawings_required,
        description=description.strip() or None,
        notes=notes.strip() or None,
        status=QuoteStatus.draft,
        created_by_id=user.id,
    )
    db.add(quote)
    db.flush()

    # Parse line items
    idx = 0
    total = 0.0
    while f"li_desc_{idx}" in form:
        desc = form.get(f"li_desc_{idx}", "").strip()
        if desc:
            qty = float(form.get(f"li_qty_{idx}", 1) or 1)
            price = float(form.get(f"li_price_{idx}") or 0)
            paint_val = form.get(f"li_paint_{idx}", "").strip()
            li = QuoteLineItem(
                quote_id=quote.id,
                line_number=idx + 1,
                description=desc,
                quantity=qty,
                unit=form.get(f"li_unit_{idx}", "").strip() or None,
                material=form.get(f"li_material_{idx}", "").strip() or None,
                unit_price=price or None,
                paint_override=None if paint_val in ("", "Same as Job") else paint_val,
                notes=form.get(f"li_notes_{idx}", "").strip() or None,
            )
            db.add(li)
            total += qty * price
        idx += 1

    quote.total_estimated = round(total, 2) if total else None
    db.commit()
    return RedirectResponse(f"/quotes/{quote.id}", status_code=302)

# ── Quote Detail ──────────────────────────────────────────────────────────
@router.get("/{quote_id}", response_class=HTMLResponse)
async def quote_detail(
    request: Request,
    quote_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    quote = db.query(Quote).options(
        joinedload(Quote.customer),
        joinedload(Quote.line_items),
        joinedload(Quote.order),
        joinedload(Quote.revisions).joinedload(QuoteRevision.edited_by),
    ).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404, "Quote not found")
    check_expiry(quote)
    db.commit()
    return templates.TemplateResponse("quotes/detail.html", {
        "request": request, "user": user, "quote": quote,
        "paint_options": PAINT_OPTIONS,
        "today": date.today(),
        "can_see_financials": financials_visible(user),
    })

# ── Mark Sent ─────────────────────────────────────────────────────────────
@router.post("/{quote_id}/send")
async def mark_sent(
    quote_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    quote = db.query(Quote).options(joinedload(Quote.line_items)).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404)

    quote.status = QuoteStatus.sent
    quote.sent_at = datetime.now(timezone.utc)
    quote.valid_until = date.today() + timedelta(days=14)
    db.commit()
    return RedirectResponse(f"/quotes/{quote_id}/print", status_code=302)

# ── Print View ───────────────────────────────────────────────────────────
@router.get("/{quote_id}/print", response_class=HTMLResponse)
async def print_quote(
    quote_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    quote = db.query(Quote).options(
        joinedload(Quote.customer), joinedload(Quote.line_items),
    ).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404)
    return templates.TemplateResponse("quotes/print.html", {
        "request": request, "user": user, "quote": quote,
        "can_see_financials": financials_visible(user),
    })

# ── Accept Quote ──────────────────────────────────────────────────────────
@router.post("/{quote_id}/accept")
async def accept_quote(
    quote_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    quote = db.query(Quote).options(
        joinedload(Quote.line_items),
        joinedload(Quote.customer),
    ).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404)
    if quote.order:
        return RedirectResponse(f"/orders/{quote.order.id}", status_code=302)

    quote.status = QuoteStatus.converted

    # Auto-create order — promised_date left blank, must be set before production
    from app.routers.orders import next_order_number
    order_num = next_order_number(db)
    order = Order(
        order_number=order_num,
        customer_id=quote.customer_id,
        quote_id=quote.id,
        job_type=quote.job_type,
        priority=quote.priority or "standard",
        status=OrderStatus.confirmed,
        paint_spec=quote.paint_spec,
        drawings_required=quote.drawings_required,
        description=quote.description,
        notes=f"[Converted from {quote.quote_number}]\n{quote.notes or ''}".strip(),
        promised_date=None,   # must be finalized
        created_by_id=user.id,
    )
    db.add(order)
    db.flush()

    # Transfer line items
    for idx, li in enumerate(quote.line_items):
        db.add(OrderLineItem(
            order_id=order.id,
            line_number=idx + 1,
            description=li.description,
            quantity=li.quantity,
            unit_price=li.unit_price,
            paint_override=None,
        ))

    # Create production stages
    stages = [StageType.material_receiving, StageType.fabrication, StageType.qa_qc, StageType.delivery]
    if quote.drawings_required:
        stages.insert(1, StageType.drawings)
    for s in stages:
        db.add(ProductionStage(order_id=order.id, stage_type=s))

    db.commit()
    return RedirectResponse(f"/orders/{order.id}", status_code=302)

# ── Decline Quote ─────────────────────────────────────────────────────────
@router.post("/{quote_id}/decline")
async def decline_quote(
    quote_id: int,
    decline_reason: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404)
    quote.status = QuoteStatus.declined
    quote.decline_reason = decline_reason.strip() or None
    db.commit()
    return RedirectResponse(f"/quotes/{quote_id}", status_code=302)

# ── Edit Quote ───────────────────────────────────────────────────────────
@router.get("/{quote_id}/edit", response_class=HTMLResponse)
async def edit_quote_form(
    quote_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    quote = db.query(Quote).options(
        joinedload(Quote.customer), joinedload(Quote.line_items)
    ).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404)
    if quote.status not in [QuoteStatus.draft, QuoteStatus.sent]:
        raise HTTPException(400, "Only draft or sent quotes can be edited")
    customers = db.query(Customer).filter(Customer.is_active == True).order_by(Customer.name).all()
    from app.models.order import JobType, Priority
    return templates.TemplateResponse("quotes/edit.html", {
        "request": request, "user": user, "quote": quote,
        "customers": customers, "job_types": JobType, "priorities": Priority,
        "paint_options": PAINT_OPTIONS, "today": date.today().isoformat(),
        "can_see_financials": financials_visible(user),
    })

@router.post("/{quote_id}/edit")
async def edit_quote(
    quote_id: int,
    request: Request,
    customer_id: int = Form(...),
    job_type: str = Form(...),
    priority: str = Form("standard"),
    paint_spec: str = Form(""),
    drawings_required: bool = Form(False),
    description: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    quote = db.query(Quote).options(joinedload(Quote.line_items)).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404)
    if quote.status not in [QuoteStatus.draft, QuoteStatus.sent]:
        raise HTTPException(400, "Cannot edit this quote")
    form = await request.form()
    # Snapshot current state before overwriting
    snapshot = {
        "revision": quote.revision,
        "customer_id": quote.customer_id,
        "job_type": quote.job_type,
        "priority": quote.priority,
        "paint_spec": quote.paint_spec,
        "drawings_required": quote.drawings_required,
        "description": quote.description,
        "notes": quote.notes,
        "total_estimated": quote.total_estimated,
        "line_items": [
            {"line_number": li.line_number, "description": li.description,
             "quantity": li.quantity, "unit_price": li.unit_price, "paint_override": li.paint_override, "notes": li.notes}
            for li in sorted(quote.line_items, key=lambda x: x.line_number)
        ],
    }
    db.add(QuoteRevision(
        quote_id=quote.id,
        revision_number=quote.revision,
        snapshot=snapshot,
        edited_by_id=user.id,
        change_note=None,
    ))
    quote.revision = (quote.revision or 1) + 1
    # Apply edits
    quote.customer_id = customer_id
    quote.job_type = job_type
    quote.priority = priority
    quote.paint_spec = paint_spec or None
    quote.drawings_required = drawings_required
    quote.description = description.strip() or None
    quote.notes = notes.strip() or None
    # Replace line items
    for li in quote.line_items:
        db.delete(li)
    db.flush()
    idx = 0
    total = 0.0
    while f"li_desc_{idx}" in form:
        desc = form.get(f"li_desc_{idx}", "").strip()
        if desc:
            qty = float(form.get(f"li_qty_{idx}", 1) or 1)
            price = float(form.get(f"li_price_{idx}") or 0)
            paint_val = form.get(f"li_paint_{idx}", "").strip()
            db.add(QuoteLineItem(
                quote_id=quote.id,
                line_number=idx + 1,
                description=desc,
                quantity=qty,
                unit=form.get(f"li_unit_{idx}", "").strip() or None,
                material=form.get(f"li_material_{idx}", "").strip() or None,
                unit_price=price or None,
                paint_override=None if paint_val in ("", "Same as Job") else paint_val,
                notes=form.get(f"li_notes_{idx}", "").strip() or None,
            ))
            total += qty * price
        idx += 1
    quote.total_estimated = round(total, 2) if total else None
    db.commit()
    return RedirectResponse(f"/quotes/{quote_id}", status_code=302)

# ── Customer search (reuse orders endpoint) ───────────────────────────────
@router.get("/search/customers", response_class=HTMLResponse)
async def customer_search(
    _customer_search: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    q = _customer_search.strip()
    if len(q) < 2:
        return HTMLResponse("")
    results = db.query(Customer).filter(
        (Customer.name.ilike(f"%{q}%") | Customer.phone.ilike(f"%{q}%")),
        Customer.is_active == True,
    ).limit(8).all()
    if not results:
        return HTMLResponse('<div class="px-3 py-2 text-sm text-gray-400">No customers found</div>')
    html = ""
    for c in results:
        company = f'  <span class="text-gray-400 text-xs">{c.company}</span>' if c.company else ""
        phone = f'  <span class="text-gray-400 text-xs">{c.phone}</span>' if c.phone else ""
        html += f'<div data-customer-id="{c.id}" data-customer-name="{c.name}" class="px-3 py-2 cursor-pointer hover:bg-steel-light text-sm">{c.name}{company}{phone}</div>'
    return HTMLResponse(html)