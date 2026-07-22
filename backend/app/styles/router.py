from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.rbac import Role, require_roles
from .models import (
    BomLineRead,
    BomStatus,
    BomVersion,
    BomVersionCreate,
    BomVersionRead,
    SizeConsumptionRead,
    Style,
    StyleColourway,
    StyleColourwayCreate,
    StyleColourwayRead,
    StyleCreate,
    StyleRead,
    StyleUpdate,
)
from .service import BomError, approve_bom_version, create_bom_version

router = APIRouter(prefix="/styles", tags=["styles"])


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Duplicate or invalid record") from exc


# --------------------------------------------------------------------------- #
# Style master (1.7)
# --------------------------------------------------------------------------- #
@router.post("", response_model=StyleRead, status_code=201)
def create_style(
    payload: StyleCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    style = Style.model_validate(payload)
    session.add(style)
    _commit(session)
    session.refresh(style)
    return style


@router.get("", response_model=List[StyleRead])
def list_styles(session: Session = Depends(get_session)):
    return session.exec(select(Style)).all()


@router.get("/{style_id}", response_model=StyleRead)
def get_style(style_id: int, session: Session = Depends(get_session)):
    style = session.get(Style, style_id)
    if style is None:
        raise HTTPException(status_code=404, detail="Style not found")
    return style


@router.patch("/{style_id}", response_model=StyleRead)
def update_style(
    style_id: int,
    payload: StyleUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    style = session.get(Style, style_id)
    if style is None:
        raise HTTPException(status_code=404, detail="Style not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(style, field, value)
    session.add(style)
    _commit(session)
    session.refresh(style)
    return style


# --------------------------------------------------------------------------- #
# Colourways (1.8)
# --------------------------------------------------------------------------- #
@router.post("/{style_id}/colourways", response_model=StyleColourwayRead, status_code=201)
def add_colourway(
    style_id: int,
    payload: StyleColourwayCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    style = session.get(Style, style_id)
    if style is None:
        raise HTTPException(status_code=404, detail="Style not found")
    colourway = StyleColourway.model_validate(payload, update={"style_id": style_id})
    session.add(colourway)
    _commit(session)
    session.refresh(colourway)
    return colourway


@router.get("/{style_id}/colourways", response_model=List[StyleColourwayRead])
def list_colourways(style_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(StyleColourway).where(StyleColourway.style_id == style_id)
    ).all()


@router.post(
    "/colourways/{colourway_id}/approve-lab-dip",
    response_model=StyleColourwayRead,
)
def approve_lab_dip(
    colourway_id: int,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.quality_inspector, Role.merchandiser)),
):
    colourway = session.get(StyleColourway, colourway_id)
    if colourway is None:
        raise HTTPException(status_code=404, detail="Colourway not found")
    colourway.lab_dip_approved = True
    session.add(colourway)
    _commit(session)
    session.refresh(colourway)
    return colourway


# --------------------------------------------------------------------------- #
# BOM versions (1.9 / 1.10)
# --------------------------------------------------------------------------- #
def _bom_read(version: BomVersion) -> BomVersionRead:
    return BomVersionRead(
        id=version.id,
        style_id=version.style_id,
        version_no=version.version_no,
        status=version.status,
        notes=version.notes,
        approved_by=version.approved_by,
        approved_at=version.approved_at,
        lines=[
            BomLineRead(
                id=line.id,
                material_id=line.material_id,
                colour_id=line.colour_id,
                wastage_pct=line.wastage_pct,
                optional_component=line.optional_component,
                notes=line.notes,
                size_consumption=[
                    SizeConsumptionRead(
                        size_label=c.size_label,
                        position=c.position,
                        consumption=c.consumption,
                    )
                    for c in line.size_consumption
                ],
            )
            for line in version.lines
        ],
    )


@router.post("/{style_id}/bom-versions", response_model=BomVersionRead, status_code=201)
def create_bom(
    style_id: int,
    payload: BomVersionCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    style = session.get(Style, style_id)
    if style is None:
        raise HTTPException(status_code=404, detail="Style not found")
    try:
        version = create_bom_version(session, style, payload)
    except BomError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _commit(session)
    session.refresh(version)
    return _bom_read(version)


@router.get("/{style_id}/bom-versions", response_model=List[BomVersionRead])
def list_bom_versions(style_id: int, session: Session = Depends(get_session)):
    versions = session.exec(
        select(BomVersion)
        .where(BomVersion.style_id == style_id)
        .order_by(BomVersion.version_no)
    ).all()
    return [_bom_read(v) for v in versions]


@router.get("/bom-versions/{version_id}", response_model=BomVersionRead)
def get_bom_version(version_id: int, session: Session = Depends(get_session)):
    version = session.get(BomVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="BOM version not found")
    return _bom_read(version)


@router.post("/bom-versions/{version_id}/approve", response_model=BomVersionRead)
def approve_bom(
    version_id: int,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.merchandiser)),
):
    version = session.get(BomVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="BOM version not found")
    if version.status != BomStatus.draft:
        raise HTTPException(
            status_code=409, detail=f"Cannot approve a '{version.status.value}' version"
        )
    approve_bom_version(session, version, actor)
    _commit(session)
    session.refresh(version)
    return _bom_read(version)
