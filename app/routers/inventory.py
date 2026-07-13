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
    # Carbon Steel
    "steel_pipe":           "Steel — Pipe (Sched 40 / DOM)",
    "steel_rect_tube":      "Steel — Rectangular Tubing",
    "steel_sq_tube":        "Steel — Square Tubing",
    "steel_channel":        "Steel — Channel",
    "steel_bar":            "Steel — Bar / Strip / Angle",
    "steel_ibeam":          "Steel — I-Beam",
    "wide_flange":          "Wide Flange Beams",
    "columns":              "Columns",
    "steel_plate":          "Steel — Plate",
    "steel_sheet":          "Steel — Sheet (incl. Galvanized)",
    # Aluminum
    "aluminum_structural":  "Aluminum — Structural",
    "aluminum_sheet":       "Aluminum — Sheet",
    # Stainless
    "stainless_structural": "Stainless — Structural",
    "stainless_sheet":      "Stainless — Sheet",
    # Other
    "misc":                 "Miscellaneous",
    "bumper_posts":         "Bumper Posts",
    "consumables":          "Consumables",
    "hardware":             "Hardware",
    "retail":               "Retail / Walk-In",
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

    # Log initial stock if >