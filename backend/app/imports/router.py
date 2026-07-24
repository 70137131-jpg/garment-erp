"""Admin-only CSV import endpoints for onboarding master data and stock."""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlmodel import Session

from ..db import get_session
from ..kernel.rbac import Role, require_roles
from .service import IMPORTERS, TEMPLATES, ImportReport, import_opening_stock

router = APIRouter(prefix="/imports", tags=["imports"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


@router.get("/templates/{kind}", response_class=PlainTextResponse)
def download_template(kind: str, _: str = Depends(require_roles(Role.admin))):
    """Header-only CSV template for the given import kind."""
    if kind not in TEMPLATES:
        raise HTTPException(status_code=404, detail=f"Unknown import kind '{kind}'")
    return PlainTextResponse(
        ",".join(TEMPLATES[kind]) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{kind}-template.csv"'},
    )


@router.post("/{kind}", response_model=ImportReport)
async def run_import(
    kind: str,
    file: UploadFile,
    apply: bool = Query(
        default=False,
        description="false = validate only (dry run); true = import when the file is error-free",
    ),
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.admin)),
):
    """Validate (and optionally apply) a CSV import.

    The import is all-or-nothing: with ``apply=true`` rows are only written
    when the whole file validates, so a corrected file can safely be re-run.
    """
    if kind not in IMPORTERS:
        raise HTTPException(status_code=404, detail=f"Unknown import kind '{kind}'")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Import file is too large (2 MB limit)")
    if kind == "opening-stock":
        return import_opening_stock(session, content, apply=apply, actor=actor)
    return IMPORTERS[kind](session, content, apply=apply)
