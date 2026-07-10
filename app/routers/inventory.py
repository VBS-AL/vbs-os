from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.database import get_db
from app.auth import get_current_user, financials_visible
from app.models.inventory import (
    InventoryItem, InventoryAdjustment,
    InventoryCategory, AdjustmentReason,
)
from app.models.settings import AppSetting

router = APIRouter(prefix="/inventory", tags=["inventory"])
templates = Jinja2Templates(directory="app/templates")

CATEGORY_LABELS = {
    "plate":       "Plate",
    "structural":  "Structural Steel",
    "beam":        "Beam",
    "consumables": "Consumables",
    "hardware":    "Hardware",
}

REASON_LABELS = {
    "received":   "Received from Supplier",
    "used":       "Used in Production",
    "damaged":    "Damaged / Scrapped",
    "correction": "Count Correction",
    "returned":   "Returned to Supplier",
    "other":      "Other",
}


def get_category_markups(db: Session) -> dict:
    """Return {category_value: markup_percent} from app_settings."""
    rows = db.query(AppSetting).filter(AppSetting.key.like("markup.%")).all()
    result = {cat.value: 0.0 for cat in InventoryCategory}
    for row in rows:
        cat = row.key.replace("markup.", "")
        try:
            result[cat] = float(row.value or 0)
        except ValueError:
            pass
    return result


def next_sku(db: Session) -> str:
    last = db.query(InventoryItem.sku).order_by(InventoryItem.sku.desc()).first()
    if last and last[0]:
        try:
            num = int(last[0].split("-")[-1]) + 1
        except Exception:
            num = 1
    else:
        num = 1
    return f"VBS-INV-{num:05d}"


# ── Search (HTMX autocomplete for order line items) ───────────────────────
@router.get("/search", response_class=HTMLResponse)
async def search_inventory(
    q: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user or len(q.strip()) < 1:
        return HTMLResponse("")
    items = db.query(InventoryItem).filter(
        InventoryItem.is_active == True,
        (InventoryItem.name.ilike(f"%{q}%") | InventoryItem.sku.ilike(f"%{q}%")),
    ).order_by(InventoryItem.name).limit(10).all()

    if not items:
        return HTMLResponse('<div class="px-3 py-2 text-sm text-gray-400">No items found</div>')

    markups = get_category_markups(db)

    rows = ""
    for item in items:
        cost = item.cost_per_unit or 0
        markup_pct = markups.get(item.category.value, 0)
        sell_price = round(cost * (1 + markup_pct / 100), 2) if cost else ""
        price_str = f"${cost:.2f}/{item.unit}" if cost else item.unit
        qty = item.quantity_on_hand or 0
        qty_str = f"{int(qty) if qty == int(qty) else qty} {item.unit} on hand"
        low = item.reorder_threshold is not None and qty <= item.reorder_threshold
        qty_color = "text-red-500" if low else "text-gray-400"
        rows += (
            f'<div class="px-3 py-2 hover:bg-steel-light cursor-pointer text-sm flex justify-between items-center"'
            f' data-inv-id="{item.id}"'
            f' data-inv-name="{item.name}"'
            f' data-inv-sku="{item.sku or ""}"'
            f' data-inv-unit="{item.unit}"'
            f' data-inv-cost="{item.cost_per_unit or ""}"'
            f' data-inv-price="{sell_price}"'
            f' data-inv-category="{item.category.value}">'
            f'  <span><span class="font-mono text-xs text-gray-400 mr-2">{item.sku or ""}</span>{item.name}</span>'
            f'  <span class="text-xs text-right"><span class="{qty_color} block">{qty_str}</span><span class="text-gray-400">{price_str} cost</span></span>'
            f'</div>'
        )
    return HTMLResponse(rows)


# ── List ──────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
async def list_inventory(
    request: Request,
    category: str = "",
    stock_status: str = "",   # "" | "ok" | "low" | "out"
    q: str = "",
    sort_by: str = "",
    sort_dir: str = "asc",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)

    query = db.query(InventoryItem).filter(InventoryItem.is_active == True)

    if category:
        query = query.filter(InventoryItem.category == category)
    if q:
        query = query.filter(
            InventoryItem.name.ilike(f"%{q}%") | InventoryItem.sku.ilike(f"%{q}%")
        )

    sort_cols = {
        "sku":      InventoryItem.sku,
        "name":     InventoryItem.name,
        "category": InventoryItem.category,
        "location": InventoryItem.location,
        "on_hand":  InventoryItem.quantity_on_hand,
        "reorder":  InventoryItem.reorder_threshold,
        "cost":     InventoryItem.cost_per_unit,
    }
    if sort_by in sort_cols:
        col = sort_cols[sort_by]
        query = query.order_by(col.desc() if sort_dir == "desc" else col.asc())
    else:
        query = query.order_by(InventoryItem.category, InventoryItem.name)

    items = query.all()

    # Stock status filter (Python-side — uses computed threshold comparison)
    if stock_status == "low":
        items = [i for i in items if i.reorder_threshold is not None and i.quantity_on_hand <= i.reorder_threshold]
    elif stock_status == "out":
        items = [i for i in items if (i.quantity_on_hand or 0) <= 0]
    elif stock_status == "ok":
        items = [i for i in items if (i.quantity_on_hand or 0) > 0 and not (i.reorder_threshold is not None and i.quantity_on_hand <= i.reorder_threshold)]

    low_stock_count = sum(
        1 for i in db.query(InventoryItem).filter(InventoryItem.is_active == True).all()
        if i.reorder_threshold is not None and i.quantity_on_hand <= i.reorder_threshold
    )

    ctx = {
        "request":          request,
        "user":             user,
        "can_see_financials": financials_visible(user),
        "items":            items,
        "category_filter":  category,
        "stock_status":     stock_status,
        "search":           q,
        "sort_by":          sort_by,
        "sort_dir":         sort_dir,
        "low_stock_count":  low_stock_count,
        "category_labels":  CATEGORY_LABELS,
        "categories":       list(InventoryCategory),
    }

    # Return only table rows for HTMX requests
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("inventory/_rows.html", ctx)

    return templates.TemplateResponse("inventory/list.html", ctx)


# ── New ───────────────────────────────────────────────────────────────────
@router.get("/new", response_class=HTMLResponse)
async def new_item_form(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    return templates.TemplateResponse("inventory/new.html", {
        "request":         request,
        "user":            user,
        "can_see_financials": financials_visible(user),
        "categories":      list(InventoryCategory),
        "category_labels": CATEGORY_LABELS,
        "next_sku":        next_sku(db),
    })


@router.post("/new", response_class=HTMLResponse)
async def create_item(
    request: Request,
    sku: str                    = Form(...),
    name: str                   = Form(...),
    category: str               = Form(...),
    description: Optional[str]  = Form(None),
    unit: str                   = Form(...),
    quantity_on_hand: float     = Form(0),
    reorder_threshold: Optional[float] = Form(None),
    cost_per_unit: Optional[float]     = Form(None),
    location: Optional[str]     = Form(None),
    supplier_name: Optional[str] = Form(None),
    supplier_contact: Optional[str] = Form(None),
    notes: Optional[str]        = Form(None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)

    item = InventoryItem(
        sku=sku,
        name=name,
        category=InventoryCategory(category),
        description=description,
        unit=unit,
        quantity_on_hand=quantity_on_hand,
        reorder_threshold=reorder_threshold,
        cost_per_unit=cost_per_unit,
        location=location,
        supplier_name=supplier_name,
        supplier_contact=supplier_contact,
        notes=notes,
    )
    db.add(item)
    db.flush()

    # Log initial stock if > 0
    if quantity_on_hand and quantity_on_hand > 0:
        adj = InventoryAdjustment(
            item_id=item.id,
            delta=quantity_on_hand,
            reason=AdjustmentReason.received,
            notes="Initial stock on hand",
            recorded_by_id=user.id,
        )
        db.add(adj)

    db.commit()
    return RedirectResponse(f"/inventory/{item.id}", status_code=302)


# ── Markup Settings ───────────────────────────────────────────────────────
@router.get("/settings", response_class=HTMLResponse)
async def markup_settings_page(request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    markups = get_category_markups(db)
    return templates.TemplateResponse("inventory/settings.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
        "markups": markups,
        "category_labels": CATEGORY_LABELS,
        "categories": list(InventoryCategory),
        "saved": request.query_params.get("saved") == "1",
    })


@router.post("/settings")
async def save_markup_settings(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    form = await request.form()
    for cat in InventoryCategory:
        key = f"markup.{cat.value}"
        val = str(form.get(f"markup_{cat.value}", "0")).strip()
        try:
            float(val)
        except ValueError:
            val = "0"
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        if setting:
            setting.value = val
        else:
            db.add(AppSetting(key=key, value=val))
    db.commit()
    return RedirectResponse("/inventory/settings?saved=1", status_code=302)


# ── Excel helpers ─────────────────────────────────────────────────────────
IMPORT_COLS = [
    "SKU", "Name", "Category", "Unit",
    "Quantity On Hand", "Reorder At", "Unit Cost ($)",
    "Location", "Supplier Name", "Supplier Contact", "Notes",
]
AUDIT_COLS = [
    "SKU", "Name", "Category", "Location",
    "System Qty", "Counted Qty", "Unit", "Notes",
]
HDR_FILL  = PatternFill("solid", fgColor="1B3A4B")
HDR_FONT  = Font(bold=True, color="FFFFFF")
LOCK_FILL = PatternFill("solid", fgColor="F0F4F7")
LOCK_FONT = Font(color="888888")

def _style_header(ws, ncols: int):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(c)].width = 20


# ── Template downloads ─────────────────────────────────────────────────────
@router.get("/template/import")
async def download_import_template(user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inventory Import"
    for c, h in enumerate(IMPORT_COLS, 1):
        ws.cell(row=1, column=c, value=h)
    _style_header(ws, len(IMPORT_COLS))
    # Example row
    example = [
        "", '1/2" A36 Steel Plate', "plate", "lbs",
        500, 100, 0.85, "Plate / Rack A", "Steel Supply Co.", "555-1234", "",
    ]
    for c, v in enumerate(example, 1):
        ws.cell(row=2, column=c, value=v)
    # Helper note
    ws.cell(row=3, column=1, value="← Delete example row before importing. Leave SKU blank to auto-generate.")
    ws.cell(row=3, column=1).font = Font(italic=True, color="888888")
    ws.merge_cells(f"A3:{get_column_letter(len(IMPORT_COLS))}3")
    # Category note
    ws.cell(row=4, column=3, value="plate | structural | beam | consumables | hardware")
    ws.cell(row=4, column=3).font = Font(italic=True, color="888888")
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return StreamingResponse(buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=vbs_inventory_import_template.xlsx"})


@router.get("/template/audit")
async def download_audit_template(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    items = (db.query(InventoryItem)
               .filter(InventoryItem.is_active == True)
               .order_by(InventoryItem.category, InventoryItem.name)
               .all())
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Physical Count"
    for c, h in enumerate(AUDIT_COLS, 1):
        ws.cell(row=1, column=c, value=h)
    _style_header(ws, len(AUDIT_COLS))
    for r, item in enumerate(items, 2):
        vals = [
            item.sku, item.name,
            CATEGORY_LABELS.get(item.category.value, item.category.value),
            item.location or "",
            item.quantity_on_hand, None,   # Counted Qty blank for user
            item.unit, "",
        ]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=v)
            if c != 6:   # lock everything except Counted Qty
                cell.fill = LOCK_FILL
                cell.font = LOCK_FONT
    # Bold the Counted Qty header to call it out
    ws.cell(row=1, column=6).font = Font(bold=True, color="FFAA00")
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    fname = f"vbs_physical_count_{date.today().strftime('%Y-%m-%d')}.xlsx"
    return StreamingResponse(buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"})


# ── Import page ────────────────────────────────────────────────────────────
@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request, user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    return templates.TemplateResponse("inventory/import.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
    })


@router.post("/import", response_class=HTMLResponse)
async def process_import(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)

    contents = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(contents))
    except Exception:
        return templates.TemplateResponse("inventory/import.html", {
            "request": request, "user": user,
            "can_see_financials": financials_visible(user),
            "error": "Could not read file — make sure it's a valid .xlsx file.",
        })

    ws = wb.active
    headers = [str(cell.value).strip() if cell.value else "" for cell in ws[1]]

    created, updated, errors = [], [], []
    CATS = {c.value for c in InventoryCategory}

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(row):
            continue
        rd = dict(zip(headers, row))

        name = str(rd.get("Name") or "").strip()
        if not name or name.lower().startswith("←") or name.lower().startswith("delete"):
            continue

        cat_raw = str(rd.get("Category") or "").strip().lower()
        if cat_raw not in CATS:
            errors.append(f"Row {row_idx} ({name or '—'}): invalid category '{cat_raw}'. "
                          f"Must be: {', '.join(sorted(CATS))}")
            continue

        unit = str(rd.get("Unit") or "each").strip() or "each"

        def _float(key):
            v = rd.get(key)
            try:
                return float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        qty       = _float("Quantity On Hand") or 0.0
        reorder   = _float("Reorder At")
        cost      = _float("Unit Cost ($)")
        location  = str(rd.get("Location") or "").strip() or None
        sup_name  = str(rd.get("Supplier Name") or "").strip() or None
        sup_con   = str(rd.get("Supplier Contact") or "").strip() or None
        notes_val = str(rd.get("Notes") or "").strip() or None
        sku       = str(rd.get("SKU") or "").strip()

        existing = db.query(InventoryItem).filter(InventoryItem.sku == sku).first() if sku else None

        if existing:
            existing.name              = name
            existing.category          = InventoryCategory(cat_raw)
            existing.unit              = unit
            existing.quantity_on_hand  = qty
            existing.reorder_threshold = reorder
            existing.cost_per_unit     = cost
            existing.location          = location
            existing.supplier_name     = sup_name
            existing.supplier_contact  = sup_con
            existing.notes             = notes_val
            updated.append({"sku": sku, "name": name})
        else:
            if not sku:
                sku = next_sku(db)
            item = InventoryItem(
                sku=sku, name=name,
                category=InventoryCategory(cat_raw),
                unit=unit, quantity_on_hand=qty,
                reorder_threshold=reorder, cost_per_unit=cost,
                location=location, supplier_name=sup_name,
                supplier_contact=sup_con, notes=notes_val,
            )
            db.add(item)
            db.flush()
            if qty > 0:
                db.add(InventoryAdjustment(
                    item_id=item.id, delta=qty,
                    reason=AdjustmentReason.received,
                    notes="Initial stock — bulk import",
                    recorded_by_id=user.id,
                ))
            created.append({"sku": sku, "name": name})

    db.commit()
    return templates.TemplateResponse("inventory/import_result.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
        "created": created, "updated": updated, "errors": errors,
    })


# ── Audit / Physical Count ─────────────────────────────────────────────────
@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request, user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    return templates.TemplateResponse("inventory/audit.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
    })


@router.post("/audit", response_class=HTMLResponse)
async def process_audit(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)

    contents = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(contents))
    except Exception:
        return templates.TemplateResponse("inventory/audit.html", {
            "request": request, "user": user,
            "can_see_financials": financials_visible(user),
            "error": "Could not read file — make sure it's a valid .xlsx file.",
        })

    ws = wb.active
    # Counted Qty is always column 6 (index 5) in our template
    adjusted, no_change, not_found, errors = [], [], [], []

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        sku = str(row[0] or "").strip() if row[0] is not None else ""
        if not sku:
            continue
        counted_raw = row[5]   # column F = Counted Qty
        if counted_raw is None or counted_raw == "":
            continue
        try:
            counted = float(counted_raw)
        except (TypeError, ValueError):
            errors.append(f"Row {row_idx} ({sku}): '{counted_raw}' is not a valid number")
            continue

        item = db.query(InventoryItem).filter(InventoryItem.sku == sku).first()
        if not item:
            not_found.append(sku)
            continue

        old_qty = item.quantity_on_hand or 0.0
        if abs(counted - old_qty) < 0.0001:
            no_change.append({"sku": sku, "name": item.name, "qty": old_qty})
            continue

        delta = counted - old_qty
        notes_raw = str(row[7] or "").strip() if len(row) > 7 else ""
        audit_note = f"Physical count — system: {old_qty} {item.unit}, counted: {counted} {item.unit}."
        if notes_raw:
            audit_note += f" Note: {notes_raw}"

        item.quantity_on_hand = counted
        db.add(InventoryAdjustment(
            item_id=item.id, delta=delta,
            reason=AdjustmentReason.correction,
            notes=audit_note,
            recorded_by_id=user.id,
        ))
        adjusted.append({
            "sku": sku, "name": item.name,
            "old": old_qty, "new": counted,
            "delta": delta, "unit": item.unit,
        })

    db.commit()
    return templates.TemplateResponse("inventory/audit_result.html", {
        "request": request, "user": user,
        "can_see_financials": financials_visible(user),
        "adjusted": adjusted, "no_change": no_change,
        "not_found": not_found, "errors": errors,
    })


# ── Detail ────────────────────────────────────────────────────────────────
@router.get("/{item_id}", response_class=HTMLResponse)
async def item_detail(
    request: Request,
    item_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        return RedirectResponse("/inventory", status_code=302)

    return templates.TemplateResponse("inventory/detail.html", {
        "request":         request,
        "user":            user,
        "can_see_financials": financials_visible(user),
        "item":            item,
        "category_labels": CATEGORY_LABELS,
        "reason_labels":   REASON_LABELS,
        "reasons":         list(AdjustmentReason),
    })


# ── Adjust stock ──────────────────────────────────────────────────────────
@router.post("/{item_id}/adjust")
async def adjust_stock(
    item_id: int,
    action: str             = Form(...),   # "add" or "remove"
    amount: float           = Form(...),
    reason: str             = Form(...),
    notes: Optional[str]    = Form(None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        return RedirectResponse("/inventory", status_code=302)

    delta = amount if action == "add" else -amount
    item.quantity_on_hand = (item.quantity_on_hand or 0) + delta

    adj = InventoryAdjustment(
        item_id=item.id,
        delta=delta,
        reason=AdjustmentReason(reason),
        notes=notes,
        recorded_by_id=user.id,
    )
    db.add(adj)
    db.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=302)


# ── Edit ──────────────────────────────────────────────────────────────────
@router.get("/{item_id}/edit", response_class=HTMLResponse)
async def edit_item_form(
    request: Request,
    item_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        return RedirectResponse("/inventory", status_code=302)
    return templates.TemplateResponse("inventory/edit.html", {
        "request":         request,
        "user":            user,
        "can_see_financials": financials_visible(user),
        "item":            item,
        "categories":      list(InventoryCategory),
        "category_labels": CATEGORY_LABELS,
    })


@router.post("/{item_id}/edit")
async def save_item(
    item_id: int,
    name: str                          = Form(...),
    category: str                      = Form(...),
    description: Optional[str]         = Form(None),
    unit: str                          = Form(...),
    reorder_threshold: Optional[float] = Form(None),
    cost_per_unit: Optional[float]     = Form(None),
    location: Optional[str]            = Form(None),
    supplier_name: Optional[str]       = Form(None),
    supplier_contact: Optional[str]    = Form(None),
    notes: Optional[str]               = Form(None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        return RedirectResponse("/inventory", status_code=302)

    item.name              = name
    item.category          = InventoryCategory(category)
    item.description       = description
    item.unit              = unit
    item.reorder_threshold = reorder_threshold
    item.cost_per_unit     = cost_per_unit
    item.location          = location
    item.supplier_name     = supplier_name
    item.supplier_contact  = supplier_contact
    item.notes             = notes
    db.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=302)
