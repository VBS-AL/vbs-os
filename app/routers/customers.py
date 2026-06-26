from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_user
from app.models.customer import Customer
from app.models.user import User

router = APIRouter(prefix="/customers", tags=["customers"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def list_customers(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    query = db.query(Customer).filter(Customer.is_active == True)
    if q:
        like = f"%{q}%"
        query = query.filter(
            Customer.name.ilike(like)
            | Customer.company.ilike(like)
            | Customer.phone.ilike(like)
            | Customer.email.ilike(like)
        )
    customers = query.order_by(Customer.name).all()
    return templates.TemplateResponse(
        "customers/list.html",
        {"request": request, "customers": customers, "q": q, "current_user": current_user},
    )


@router.get("/new", response_class=HTMLResponse)
def new_customer_form(
    request: Request,
    next: str = "/customers",
    current_user: User = Depends(require_user),
):
    return templates.TemplateResponse(
        "customers/new.html",
        {"request": request, "next": next, "error": None, "current_user": current_user},
    )


@router.post("/new")
def create_customer(
    request: Request,
    next: str = Form("/customers"),
    name: str = Form(...),
    company: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    name = name.strip()
    if not name:
        return templates.TemplateResponse(
            "customers/new.html",
            {"request": request, "next": next, "error": "Name is required.", "current_user": current_user},
            status_code=422,
        )
    c = Customer(
        name=name,
        company=company.strip() or None,
        phone=phone.strip() or None,
        email=email.strip() or None,
        address=address.strip() or None,
        notes=notes.strip() or None,
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
        {"request": request, "customer": c, "current_user": current_user},
    )


@router.post("/{customer_id}/edit")
def edit_customer(
    customer_id: int,
    name: str = Form(...),
    company: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Not found")
    c.name = name.strip()
    c.company = company.strip() or None
    c.phone = phone.strip() or None
    c.email = email.strip() or None
    c.address = address.strip() or None
    c.notes = notes.strip() or None
    db.commit()
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)
