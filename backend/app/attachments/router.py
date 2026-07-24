"""Attachments: upload/list/download/delete files linked to business records."""

import hashlib
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..kernel.rbac import Permission, Principal, Role, get_current_principal, require_permissions
from ..masters.models import Customer, Material, Supplier
from ..procurement.models import PurchaseOrder
from ..sales.models import SalesOrder
from ..styles.models import Style
from .models import Attachment, AttachmentRead

logger = logging.getLogger("garment_erp.attachments")

router = APIRouter(
    prefix="/attachments",
    tags=["attachments"],
    dependencies=[Depends(require_permissions(Permission.masters_read))],
)

# Entity kinds that accept attachments, with their model for existence checks.
ENTITY_MODELS = {
    "material": Material,
    "customer": Customer,
    "supplier": Supplier,
    "style": Style,
    "sales_order": SalesOrder,
    "purchase_order": PurchaseOrder,
}

# Extension allowlist: documents and images a factory realistically attaches.
ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".xlsx", ".xls", ".csv", ".docx", ".doc", ".txt",
}


def _storage_root() -> Path:
    root = Path(settings.attachments_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _get_or_404(session: Session, attachment_id: int) -> Attachment:
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return attachment


def _validate_entity(session: Session, entity_type: str, entity_id: int) -> None:
    model = ENTITY_MODELS.get(entity_type)
    if model is None:
        raise HTTPException(
            status_code=422,
            detail=f"entity_type must be one of: {', '.join(sorted(ENTITY_MODELS))}",
        )
    if session.get(model, entity_id) is None:
        raise HTTPException(status_code=404, detail=f"No {entity_type} with id {entity_id}")


@router.post("", response_model=AttachmentRead, status_code=201)
async def upload_attachment(
    file: UploadFile,
    entity_type: str = Query(...),
    entity_id: int = Query(...),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
):
    _validate_entity(session, entity_type, entity_id)

    original_name = Path(file.filename or "upload").name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"File type '{extension or 'unknown'}' is not allowed",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="File is empty")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit",
        )

    stored_name = f"{uuid.uuid4().hex}{extension}"
    target = _storage_root() / stored_name
    target.write_bytes(content)

    attachment = Attachment(
        entity_type=entity_type,
        entity_id=entity_id,
        filename=original_name[:255],
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        stored_name=stored_name,
        uploaded_by=principal.user_id,
    )
    session.add(attachment)
    try:
        session.commit()
    except Exception:
        target.unlink(missing_ok=True)  # do not leave orphan bytes behind
        raise
    session.refresh(attachment)
    return attachment


@router.get("", response_model=list[AttachmentRead])
def list_attachments(
    entity_type: str = Query(...),
    entity_id: int = Query(...),
    session: Session = Depends(get_session),
):
    return session.exec(
        select(Attachment)
        .where(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
        .order_by(Attachment.uploaded_at)
    ).all()


@router.get("/{attachment_id}/download")
def download_attachment(attachment_id: int, session: Session = Depends(get_session)):
    attachment = _get_or_404(session, attachment_id)
    path = _storage_root() / attachment.stored_name
    if not path.is_file():
        logger.error("Attachment %s missing on disk: %s", attachment.id, path)
        raise HTTPException(status_code=410, detail="Stored file is missing")
    return FileResponse(
        path,
        media_type=attachment.content_type,
        filename=attachment.filename,
    )


@router.delete("/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: int,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
):
    """Uploader or an administrator may remove an attachment."""
    attachment = _get_or_404(session, attachment_id)
    if Role.admin not in principal.roles and attachment.uploaded_by != principal.user_id:
        raise HTTPException(status_code=403, detail="Only the uploader or an admin can delete this")
    path = _storage_root() / attachment.stored_name
    session.delete(attachment)
    session.commit()
    path.unlink(missing_ok=True)
