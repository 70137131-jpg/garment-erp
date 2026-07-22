from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..db import get_session
from ..inventory.models import Roll
from ..kernel.rbac import Role, require_roles
from .models import (
    DefectRead,
    FinalInspection,
    FinalInspectionCreate,
    FinalInspectionRead,
    FourPointInspection,
    FourPointInspectionCreate,
    FourPointInspectionRead,
    InlineInspection,
    InlineInspectionCreate,
    InlineInspectionRead,
)
from .service import (
    QualityError,
    inspect_roll,
    record_final,
    record_inline,
)

router = APIRouter(prefix="/quality", tags=["quality"])


def _read(session: Session, inspection: FourPointInspection) -> FourPointInspectionRead:
    roll = session.get(Roll, inspection.roll_id)
    return FourPointInspectionRead(
        id=inspection.id,
        inspection_number=inspection.inspection_number,
        roll_id=inspection.roll_id,
        inspected_length=inspection.inspected_length,
        width_cm=inspection.width_cm,
        acceptance_threshold=inspection.acceptance_threshold,
        total_points=inspection.total_points,
        points_per_100sqyd=inspection.points_per_100sqyd,
        result=inspection.result,
        roll_status=roll.status.value,
        defects=[
            DefectRead(
                description=d.description,
                penalty_points=d.penalty_points,
                position_m=d.position_m,
            )
            for d in inspection.defects
        ],
    )


@router.post(
    "/four-point-inspections",
    response_model=FourPointInspectionRead,
    status_code=201,
)
def create_inspection(
    payload: FourPointInspectionCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.quality_inspector)),
):
    try:
        inspection = inspect_roll(session, payload, actor)
    except QualityError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(inspection)
    return _read(session, inspection)


@router.get(
    "/four-point-inspections/{inspection_id}",
    response_model=FourPointInspectionRead,
)
def get_inspection(inspection_id: int, session: Session = Depends(get_session)):
    inspection = session.get(FourPointInspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    return _read(session, inspection)


# --------------------------------------------------------------------------- #
# Inline DHU (7.3)
# --------------------------------------------------------------------------- #
@router.post("/inline-inspections", response_model=InlineInspectionRead, status_code=201)
def create_inline(
    payload: InlineInspectionCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.quality_inspector)),
):
    try:
        inspection = record_inline(session, payload, actor)
    except QualityError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(inspection)
    return inspection


# --------------------------------------------------------------------------- #
# Final AQL (7.4)
# --------------------------------------------------------------------------- #
@router.post("/final-inspections", response_model=FinalInspectionRead, status_code=201)
def create_final(
    payload: FinalInspectionCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.quality_inspector)),
):
    try:
        inspection = record_final(session, payload, actor)
    except QualityError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(inspection)
    return inspection


@router.get("/final-inspections/{inspection_id}", response_model=FinalInspectionRead)
def get_final(inspection_id: int, session: Session = Depends(get_session)):
    inspection = session.get(FinalInspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Final inspection not found")
    return inspection
