import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_user
from app.models.user import User, UserRole
from app.models.order import Order
from app.models.production import DrawingRecord, DrawingStatus

router = APIRouter(prefix="/orders", tags=["drawings"])

MANAGEMENT_ROLES = {UserRole.owner, UserRole.ops_manager, UserRole.shop_foreman}

ALLOWED_EXTENSIONS = {".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".tiff", ".xlsx", ".docx"}

DRAWINGS_BASE = "app/static/drawings"


def _drawings_dir(order_number: str) -> str:
    return os.path.join(DRAWINGS_BASE, order_number)


@router.post("/{order_id}/drawings")
async def upload_drawings(
    order_id: int,
    files: List[UploadFile],
    display_name: Optional[str] = Form(None),
    revision: Optional[str] = Form(None),
    drawing_type: str = Form("drawing"),
    stage_context: Optional[str] = Form(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    dest_dir = _drawings_dir(order.order_number)
    os.makedirs(dest_dir, exist_ok=True)

    for upload in files:
        if not upload.filename:
            continue

        ext = os.path.splitext(upload.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type '{ext}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            )

        timestamp = int(datetime.now(timezone.utc).timestamp())
        unique_filename = f"{timestamp}_{upload.filename}"
        dest_path = os.path.join(dest_dir, unique_filename)

        content = await upload.read()
        with open(dest_path, "wb") as fh:
            fh.write(content)

        record = DrawingRecord(
            order_id=order_id,
            drawing_type=drawing_type,
            file_reference=unique_filename,
            display_name=(display_name.strip() if display_name else None) or upload.filename,
            revision=revision.strip() if revision else None,
            uploaded_by_id=user.id,
            stage_context=stage_context.strip() if stage_context else None,
            status=DrawingStatus.pending,
        )
        db.add(record)

    db.commit()
    return RedirectResponse(f"/orders/{order_id}", status_code=302)


@router.post("/{order_id}/drawings/{drawing_id}/delete")
async def delete_drawing(
    order_id: int,
    drawing_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if user.role not in MANAGEMENT_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    record = db.query(DrawingRecord).filter(
        DrawingRecord.id == drawing_id,
        DrawingRecord.order_id == order_id,
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Drawing record not found")

    order = db.query(Order).filter(Order.id == order_id).first()
    if order and record.file_reference:
        file_path = os.path.join(_drawings_dir(order.order_number), record.file_reference)
        if os.path.exists(file_path):
            os.remove(file_path)

    db.delete(record)
    db.commit()
    return RedirectResponse(f"/orders/{order_id}", status_code=302)
