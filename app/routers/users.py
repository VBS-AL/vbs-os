from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date

from app.database import get_db
from app.auth import require_management, require_user, hash_password, financials_visible
from app.models.user import User, UserRole

router = APIRouter(prefix="/users", tags=["users"])
templates = Jinja2Templates(directory="app/templates")

def next_employee_id(db: Session) -> str:
    last = db.query(User.employee_id).filter(
        User.employee_id.like("EMP-%")
    ).order_by(User.employee_id.desc()).first()
    if last:
        try:
            num = int(last[0].split("-")[1]) + 1
        except Exception:
            num = 1
    else:
        num = 1
    return f"EMP-{num:03d}"

# ── User List ─────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
async def user_list(
    request: Request,
    user: User = Depends(require_management),
    db: Session = Depends(get_db),
):
    all_users = db.query(User).order_by(User.last_name, User.first_name).all()
    active_users   = [u for u in all_users if u.is_active]
    archived_users = [u for u in all_users if not u.is_active]
    return templates.TemplateResponse("users/list.html", {
        "request": request, "user": user,
        "active_users": active_users, "archived_users": archived_users,
        "roles": UserRole,
        "can_see_financials": financials_visible(user),
    })

# ── New User ──────────────────────────────────────────────────────────────
@router.get("/new", response_class=HTMLResponse)
async def new_user_form(
    request: Request,
    user: User = Depends(require_management),
    db: Session = Depends(get_db),
):
    suggested_id = next_employee_id(db)
    return templates.TemplateResponse("users/new.html", {
        "request": request, "user": user, "roles": UserRole,
        "suggested_id": suggested_id,
        "can_see_financials": financials_visible(user),
    })

@router.post("/new")
async def create_user(
    request: Request,
    employee_id: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(""),
    email: str = Form(""),
    role: str = Form(...),
    password: str = Form(...),
    mobile_access: int = Form(0),
    user: User = Depends(require_management),
    db: Session = Depends(get_db),
):
    # Check uniqueness
    if db.query(User).filter(User.employee_id == employee_id.strip()).first():
        suggested_id = next_employee_id(db)
        return templates.TemplateResponse("users/new.html", {
            "request": request, "user": user, "roles": UserRole,
            "suggested_id": suggested_id,
            "error": f"Employee ID '{employee_id}' is already taken.",
            "form": {"employee_id": employee_id, "first_name": first_name,
                     "last_name": last_name, "email": email, "role": role},
            "can_see_financials": financials_visible(user),
        }, status_code=422)

    if email.strip() and db.query(User).filter(User.email == email.strip()).first():
        suggested_id = next_employee_id(db)
        return templates.TemplateResponse("users/new.html", {
            "request": request, "user": user, "roles": UserRole,
            "suggested_id": suggested_id,
            "error": f"Email '{email}' is already in use.",
            "form": {"employee_id": employee_id, "first_name": first_name,
                     "last_name": last_name, "email": email, "role": role},
            "can_see_financials": financials_visible(user),
        }, status_code=422)

    if len(password) < 6:
        suggested_id = next_employee_id(db)
        return templates.TemplateResponse("users/new.html", {
            "request": request, "user": user, "roles": UserRole,
            "suggested_id": suggested_id,
            "error": "Password must be at least 6 characters.",
            "form": {"employee_id": employee_id, "first_name": first_name,
                     "last_name": last_name, "email": email, "role": role},
            "can_see_financials": financials_visible(user),
        }, status_code=422)

    new_user = User(
        employee_id=employee_id.strip().upper(),
        first_name=first_name.strip(),
        last_name=last_name.strip() or None,
        email=email.strip() or None,
        hashed_password=hash_password(password),
        role=UserRole(role),
        mobile_access=bool(mobile_access),
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse("/users", status_code=302)

# ── Edit User ─────────────────────────────────────────────────────────────
@router.get("/{user_id}/edit", response_class=HTMLResponse)
async def edit_user_form(
    request: Request,
    user_id: int,
    user: User = Depends(require_management),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    return templates.TemplateResponse("users/edit.html", {
        "request": request, "user": user, "target": target, "roles": UserRole,
        "can_see_financials": financials_visible(user),
    })

@router.post("/{user_id}/edit")
async def update_user(
    request: Request,
    user_id: int,
    first_name: str = Form(...),
    last_name: str = Form(""),
    email: str = Form(""),
    role: str = Form(...),
    mobile_access: int = Form(0),
    is_active: int = Form(1),
    new_password: str = Form(""),
    hourly_cost_rate: str = Form(""),
    user: User = Depends(require_management),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404)

    # Prevent deactivating yourself
    if user_id == user.id and not is_active:
        target2 = db.query(User).filter(User.id == user_id).first()
        return templates.TemplateResponse("users/edit.html", {
            "request": request, "user": user, "target": target2, "roles": UserRole,
            "error": "You cannot deactivate your own account.",
            "can_see_financials": financials_visible(user),
        }, status_code=422)

    target.first_name = first_name.strip()
    target.last_name = last_name.strip() or None
    target.email = email.strip() or None
    target.role = UserRole(role)
    target.mobile_access = bool(mobile_access)
    target.is_active = bool(is_active)

    # hourly_cost_rate — owner-only field
    if user.role == UserRole.owner:
        try:
            target.hourly_cost_rate = float(hourly_cost_rate) if hourly_cost_rate.strip() else None
        except ValueError:
            pass

    if new_password.strip():
        if len(new_password) < 6:
            return templates.TemplateResponse("users/edit.html", {
                "request": request, "user": user, "target": target, "roles": UserRole,
                "error": "New password must be at least 6 characters.",
                "can_see_financials": f