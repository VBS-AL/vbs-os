from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from typing import Optional

from app.database import get_db
from app.auth import require_user, financials_visible
from app.models.customer import Customer
from app.models.user import User

router = APIRouter(prefix="/customers", tags=["customers"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def list_customers(
    request: Request,
    q: str = "",
    city: str = "",
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    from app.models.order import Order as OrderModel
    query = db.query(Customer).options(
        joinedload(Customer.orders).joinedload(OrderModel.line_items)
    ).filter(Customer.is_active == True)
    if q:
        like = f"%{q}%"
        query = query.filter(
            Customer.name.ilike(like)
            | Customer.company.ilike(like)
            | Customer.phone.ilike(like)
            | Customer.email.ilike(like)
        )
    if city:
        query = query.filter(Customer.city.ilike(f"%{city}%"))

    sort_map = {
        "name": Customer.name,
        "company": Customer.company,
        "city": Customer.city,
    }
    if sort_by and sort_by in sort_map:
        col = sort_map[sort_by]
        query = query.order_by(col.desc() if sort_dir == "desc" else col.asc())
    else:
        query = query.order_by(Customer.name)

    customers = query.all()

    # Python-side sorts
    if sort_by == "orders":
        customers.sort(key=lambda c: len(c.orders), reverse=(sort_dir == "desc"))
    elif sort_by == "total_value":
        customers.sort(key=lambda c: sum((li.unit_price or 0) * li.quantity for o in c.orders for li in o.line_items), reverse=(sort_dir == "desc"))

    return templates.TemplateResponse(
        "customers/list.html",
        {
            "request": request, "customers": customers, "q": q, "city": city,
            "user": current_user, "sort_by": sort_by, "sort_dir": sort_dir,
            "can_see_financials": financials_visible(current_user),
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_customer_form(
    request: Request,
    next: str = "/customers",
    current_user: User = Depends(require_user),
):
    return templates.TemplateResponse(
        "customers/new.html",
        {"request": request, "next": next, "error": None, "user": current_user,
         "can_see_financials": financials_visible(current_user)},
    )


@router.post("/new")
def create_customer(
    request: Request,
    next: str = Form("/customers"),
    name: str = Form(...),
    company: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address_line1: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    zip_code: str = Form(""),
    payment_terms: int = Form(0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    errors = []
    if not name.strip(): errors.append("Name")
    if not company.strip(): errors.append("Company")
    if not phone.strip(): errors.append("Phone")
    if not email.strip(): errors.append("Email")
    if not address_line1.strip(): errors.append("Street Address")
    if not city.strip(): errors.append("City")
    if not state.strip(): errors.append("State/Province")
    if not zip_code.strip(): errors.append("Postal Code")
    if errors:
        return templates.TemplateResponse(
            "customers/new.html",
            {"request": request, "next": next,
             "error": f"Required fields missing: {', '.join(errors)}.",
             "user": current_user,
             "can_see_financials": financials_visible(current_user)},
            status_code=422,
        )
    name = name.strip()
    c = Customer(
        name=name,
        company=company.strip() or None,
        phone=phone.strip() or None,
        email=email.strip() or None,
        address_line1=address_line1.strip() or None,
        city=city.strip() or None,
        state=state.strip().upper() or None,
        zip_code=zip_code.strip() or None,
        notes=notes.strip() or None,
        payment_terms=payment_terms,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return RedirectResponse(next, status_code=303)


@router.get("/{customer_id}", response_class=HTMLResponse)
def customer_detail(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return templates.TemplateResponse(
        "customers/detail.html",
        {"request": request, "customer": c, "user": current_user,
         "can_see_financials": financials_visible(current_user)},
    )


@router.get("/{customer_id}/edit", response_class=HTMLResponse)
def edit_customer_form(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return templates.TemplateResponse(
        "customers/edit.html",
        {"request": request, "customer": c, "user": current_user,
         "can_see_financials": financials_visible(current_user)},
    )


@router.post("/{customer_id}/edit")
def edit_customer(
    customer_id: int,
    request: Request,
    name: str = Form(...),
    company: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address_line1: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    zip_code: str = Form(""),
    payment_terms: int = Form(0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    c.name = name.strip()
    c.company = company.strip() or None
    c.phone = phone.strip() or None
    c.email = email.strip() or None
    c.address_line1 = address_line1.strip() or None
    c.city = city.strip() or None
    c.state = state.strip().upper() or None
    c.zip_code = zip_code.strip() or None
    c.notes = notes.strip() or None
    c.payment_terms = payment_terms
    db.commit()
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)
