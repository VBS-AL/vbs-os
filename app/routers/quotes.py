import json
import os
import uuid
from fastapi import APIRouter, Depends, Request, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import Optional
from datetime import date, timedelta, datetime, timezone

DRAWINGS_DIR = "app/static/drawings"
ALLOWED_DRAWING_EXT = {".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".tiff", ".xlsx", ".docx"}


def _quote_dir(quote_number: str) -> str:
    return os.path.join(DRAWINGS_DIR, quote_number)


def list_quote_files(quote_number: str) -> list:
    """Return list of dicts {filename, display_name, rel_path, ext} for files in the quote directory."""
    d = _quote_dir(quote_number)
    if not os.path.isdir(d):
        return []
    result = []
    for fname in sorted(os.listdir(d)):
        fpath = os.path.join(d, fname)
        if not os.path.isfile(fpath):
            continue
        # Strip leading timestamp prefix: "{digits}_originalname.ext"
        import re as _re
        display = _re.sub(r'^\d+_', '', fname)
        ext = os.path.splitext(fname)[1].lower().lstrip('.')
        result.append({
            "filename": fname,
            "display_name": display,
            "rel_path": f"{quote_number}/{fname}",
            "ext": ext,
        })
    return result


async def save_quote_drawings(form_data, quote_number: str) -> Optional[str]:
    """Save all uploaded files to the quote's drawing directory.
    Returns the rel_path of the first saved file (for drawing_file backward compat), or None."""
    from datetime import datetime as _dt
    uploads = form_data.getlist("files")
    dest_dir = _quote_dir(quote_number)
    os.makedirs(dest_dir, exist_ok=True)
    first_path = None
    for upload in uploads:
        if not hasattr(upload, "filename") or not upload.filename:
            continue
        ext = os.path.splitext(upload.filename)[1].lower()
        if ext not in ALLOWED_DRAWING_EXT:
            continue
        timestamp = int(_dt.utcnow().timestamp())
        unique_filename = f"{timestamp}_{upload.filename}"
        content = await upload.read()
        with open(os.path.join(dest_dir, unique_filename), "wb") as fh:
            fh.write(content)
        if first_path is None:
            first_path = f"{quote_number}/{unique_filename}"
    return first_path

from app.database import get_db
from app.auth import require_user, require_management, financials_visible
from app.models.user import User, UserRole
from app.models.quote import Quote, QuoteLineItem, QuoteStatus, QuoteRevision
from app.models.labor import BILLING_RATES, BillingDept
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
    if status == "all":
        pass  # no status filter — show everything
    elif status:
        query = query.filter(Quote.status == status)
    else:
        # default: active quotes only (draft, sent, accepted)
        query = query.filter(Quote.status.in_([QuoteStatus.draft, QuoteStatus.sent, QuoteStatus.accepted]))
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
    delivery_surcharge: str = Form(""),
    customer_po: str = Form(""),
    preferred_delivery_method: str = Form(""),
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
        preferred_delivery_method=preferred_delivery_method or None,
        drawings_required=drawings_required,
        drawing_file=None,  # set after files are saved below
        customer_po=customer_po.strip() or None,
        description=description.strip() or None,
        notes=notes.strip() or None,
        status=QuoteStatus.draft,
        created_by_id=user.id,
    )
    db.add(quote)
    db.flush()

    # Save uploaded drawing files
    first_file = await save_quote_drawings(form, quote_num)
    if first_file:
        quote.drawing_file = first_file

    # Parse line items
    idx = 0
    total = 0.0
    while f"li_desc_{idx}" in form:
        desc = form.get(f"li_desc_{idx}", "").strip()
        if desc:
            qty = float(form.get(f"li_qty_{idx}", 1) or 1)
            price = float(form.get(f"li_price_{idx}") or 0)
            paint_val = form.get(f"li_paint_{idx}", "").strip()
            inv_id_raw = form.get(f"li_inv_id_{idx}", "").strip()
            inv_id = int(inv_id_raw) if inv_id_raw else None
            labor_hrs = float(form.get(f"li_labor_{idx}") or 0)
            labor_dept = form.get(f"li_labor_dept_{idx}", "").strip() or None
            rate_snapshot = None
            if labor_dept:
                try:
                    rate_snapshot = BILLING_RATES.get(BillingDept(labor_dept))
                except Exception:
                    pass
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
                internal_notes=form.get(f"li_internal_notes_{idx}", "").strip() or None,
                inventory_item_id=inv_id,
                estimated_labor_hours=labor_hrs or None,
                estimated_labor_dept=labor_dept,
                labor_rate_snapshot=rate_snapshot,
            )
            db.add(li)
            total += qty * price
            if labor_hrs and labor_dept and rate_snapshot:
                total += labor_hrs * rate_snapshot
        idx += 1

    # Delivery surcharge
    surcharge_amount = float(delivery_surcharge) if delivery_surcharge else None
    if surcharge_amount and surcharge_amount > 0:
        db.add(QuoteLineItem(
            quote_id=quote.id, line_number=idx + 1,
            description="Delivery",
            quantity=1, unit_price=surcharge_amount,
            is_delivery_surcharge=True,
        ))
        total += surcharge_amount

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
    billing_rates = {k.value: v for k, v in BILLING_RATES.items()}
    return templates.TemplateResponse("quotes/detail.html", {
        "request": request, "user": user, "quote": quote,
        "paint_options": PAINT_OPTIONS,
        "today": date.today(),
        "can_see_financials": financials_visible(user),
        "billing_rates": billing_rates,
        "quote_files": list_quote_files(quote.quote_number),
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
    return RedirectResponse(f"/quotes/{quote_id}", status_code=302)

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
    promised_date: str = Form(...),
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
        preferred_delivery_method=quote.preferred_delivery_method,
        drawings_required=quote.drawings_required,
        drawing_file=None,  # files transferred as DrawingRecords below
        customer_po=quote.customer_po,
        description=quote.description,
        notes=f"[Converted from {quote.quote_number}]\n{quote.notes or ''}".strip(),
        promised_date=date.fromisoformat(promised_date) if promised_date else None,
        created_by_id=user.id,
    )
    db.add(order)
    db.flush()

    # Transfer line items (carry over inventory link and labor estimates)
    for idx, li in enumerate(sorted(quote.line_items, key=lambda x: x.line_number)):
        db.add(OrderLineItem(
            order_id=order.id,
            line_number=idx + 1,
            description=li.description,
            quantity=li.quantity,
            unit_price=li.unit_price,
            paint_override=None,
            inventory_item_id=li.inventory_item_id,
            estimated_labor_hours=li.estimated_labor_hours,
            estimated_labor_dept=li.estimated_labor_dept,
            internal_notes=li.internal_notes,
            is_delivery_surcharge=li.is_delivery_surcharge,
        ))

    # Create production stages — Drawings first (review before pulling material)
    if quote.drawings_required:
        stages = [StageType.drawings, StageType.material_receiving, StageType.fabrication, StageType.qa_qc, StageType.delivery]
    else:
        stages = [StageType.material_receiving, StageType.fabrication, StageType.qa_qc, StageType.delivery]
    for s in stages:
        db.add(ProductionStage(order_id=order.id, stage_type=s))

    # Transfer quote drawing files to the order as DrawingRecord entries
    from app.models.production import DrawingRecord, DrawingStatus
    import shutil
    quote_files = list_quote_files(quote.quote_number)
    if not quote_files and quote.drawing_file:
        # Legacy single file — synthesize a file entry
        quote_files = [{"filename": quote.drawing_file, "rel_path": quote.drawing_file}]
    if quote_files:
        order_dir = os.path.join(DRAWINGS_DIR, order_num)
        os.makedirs(order_dir, exist_ok=True)
        for f in quote_files:
            src = os.path.join(DRAWINGS_DIR, f["rel_path"])
            if not os.path.isfile(src):
                continue
            dest = os.path.join(order_dir, f["filename"])
            shutil.copy2(src, dest)
            db.add(DrawingRecord(
                order_id=order.id,
                drawing_type="drawing",
                file_reference=f["filename"],
                display_name=f.get("display_name", f["filename"]),
                uploaded_by_id=user.id,
                status=DrawingStatus.pending,
            ))

    db.commit()
    return RedirectResponse(f"/orders/{order.id}", status_code=302)

# ── Upload Files to Existing Quote ────────────────────────────────────────
@router.post("/{quote_id}/files")
async def upload_quote_files(
    quote_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404)
    if quote.status.value not in ["draft", "sent"]:
        raise HTTPException(400, "Cannot add files to a finalized quote")
    form = await request.form()
    await save_quote_drawings(form, quote.quote_number)
    return RedirectResponse(f"/quotes/{quote_id}#drawings", status_code=302)


# ── Delete a Quote File ────────────────────────────────────────────────────
@router.post("/{quote_id}/files/delete")
async def delete_quote_file(
    quote_id: int,
    filename: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    from app.models.user import UserRole
    if user.role not in {UserRole.owner, UserRole.ops_manager, UserRole.shop_foreman}:
        raise HTTPException(403)
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404)
    file_path = os.path.join(_quote_dir(quote.quote_number), filename)
    if os.path.isfile(file_path):
        os.remove(file_path)
    return RedirectResponse(f"/quotes/{quote_id}#drawings", status_code=302)


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
        "quote_files": list_quote_files(quote.quote_number),
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
    customer_po: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    quote = db.query(Quote).options(joinedload(Quote.line_items)).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(404)
    if quote.status not in [QuoteStatus.draft, QuoteStatus.sent]:
        raise HTTPException(400, "Cannot edit this quote")
    form = await request.form()
    new_drawing = await save_quote_drawings(form, quote.quote_number)  # None if no files uploaded
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
    if new_drawing:
        quote.drawing_file = new_drawing   # only overwrite if a new file was uploaded
    quote.customer_po = customer_po.strip() or None
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
            inv_id_raw = form.get(f"li_inv_id_{idx}", "").strip()
            inv_id = int(inv_id_raw) if inv_id_raw else None
            labor_hrs = float(form.get(f"li_labor_{idx}") or 0)
            labor_dept = form.get(f"li_labor_dept_{idx}", "").strip() or None
            rate_snapshot2 = None
            if labor_dept:
                try:
                    rate_snapshot2 = BILLING_RATES.get(BillingDept(labor_dept))
                except Exception:
                    pass
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
                internal_notes=form.get(f"li_internal_notes_{idx}", "").strip() or None,
                inventory_item_id=inv_id,
                estimated_labor_hours=labor_hrs or None,
                estimated_labor_dept=labor_dept,
                labor_rate_snapshot=rate_snapshot2,
            ))
            total += qty * price
            if labor_hrs and labor_dept and rate_snapshot2:
                total += labor_hrs * rate_snapshot2
        idx += 1
    # Delivery charge
    surcharge_raw = form.get("delivery_surcharge", "").strip()
    surcharge_amount = float(surcharge_raw) if surcharge_raw else None
    if surcharge_amount and surcharge_amount > 0:
        db.add(QuoteLineItem(
            quote_id=quote.id,
            line_number=idx + 1,
            description="Delivery",
            quantity=1,
            unit_price=surcharge_amount,
            is_delivery_surcharge=True,
        ))
        total += surcharge_amount

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
        (Customer.name.ilike(f"%{q}%") | Customer.phone.ilike(f"%{q}%") | Customer.company.ilike(f"%{q}%")),
        Customer.is_active == True,
    ).limit(8).all()
    if not results:
        return HTMLResponse('<div class="px-3 py-2 text-sm text-gray-400">No customers found</div>')
    html = ""
    for c in results:
        label = c.display_name  # company if set, otherwise name
        sub = f'  <span class="text-gray-400 text-xs">{c.name}</span>' if c.company else ""
        phone = f'  <span class="text-gray-400 text-xs">{c.phone}</span>' if c.phone else ""
        html += f'<div data-customer-id="{c.id}" data-customer-name="{label}" class="px-3 py-2 cursor-pointer hover:bg-steel-light text-sm">{label}{sub}{phone}</div>'
    return HTMLResponse(html)