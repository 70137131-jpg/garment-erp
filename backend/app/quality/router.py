from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..inventory.models import Roll
from ..kernel.rbac import Role, require_roles
from ..kernel.numbering import next_document_number
from ..kernel.query import csv_download, page_bounds
from .models import (
    DefectAnalyticsRead,
    DefectRead,
    FinalInspection,
    FinalInspectionCreate,
    FinalInspectionRead,
    FourPointDefect,
    FourPointInspection,
    FourPointInspectionCreate,
    FourPointInspectionRead,
    InlineInspection,
    InlineInspectionCreate,
    InlineInspectionRead,
    LabTest,
    LabTestComplete,
    LabTestCreate,
    LabTestRead,
    LabTestStatus,
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
                defect_code=d.defect_code,
                category=d.category,
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
    "/four-point-inspections",
    response_model=list[FourPointInspectionRead],
)
def list_four_point_inspections(
    result: Optional[str] = None,
    roll_id: Optional[int] = None,
    offset: int = 0,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    offset, limit = page_bounds(offset, limit)
    stmt = select(FourPointInspection).order_by(FourPointInspection.id.desc())
    if result is not None:
        stmt = stmt.where(FourPointInspection.result == result)
    if roll_id is not None:
        stmt = stmt.where(FourPointInspection.roll_id == roll_id)
    inspections = session.exec(stmt.offset(offset).limit(limit)).all()
    return [_read(session, inspection) for inspection in inspections]


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


@router.get("/inline-inspections", response_model=list[InlineInspectionRead])
def list_inline_inspections(session: Session = Depends(get_session)):
    return session.exec(
        select(InlineInspection).order_by(InlineInspection.id.desc())
    ).all()


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


@router.get("/final-inspections", response_model=list[FinalInspectionRead])
def list_final_inspections(session: Session = Depends(get_session)):
    return session.exec(
        select(FinalInspection).order_by(FinalInspection.id.desc())
    ).all()


@router.get("/final-inspections/{inspection_id}", response_model=FinalInspectionRead)
def get_final(inspection_id: int, session: Session = Depends(get_session)):
    inspection = session.get(FinalInspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Final inspection not found")
    return inspection


# --------------------------------------------------------------------------- #
# Lab management and defect analytics
# --------------------------------------------------------------------------- #
@router.post("/lab-tests", response_model=LabTestRead, status_code=201)
def create_lab_test(
    payload: LabTestCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.quality_inspector)),
):
    if payload.roll_id is not None:
        roll = session.get(Roll, payload.roll_id)
        if roll is None:
            raise HTTPException(status_code=404, detail="Roll not found")
        if roll.material_id != payload.material_id:
            raise HTTPException(status_code=422, detail="Roll does not match lab-test material")
    if not payload.test_type.strip():
        raise HTTPException(status_code=422, detail="Test type is required")
    test = LabTest(
        test_number=next_document_number(session, "LAB_TEST", "LAB"),
        status=LabTestStatus.pending,
        tested_by=actor,
        **payload.model_dump(),
    )
    session.add(test)
    session.commit()
    session.refresh(test)
    return test


@router.get("/lab-tests", response_model=list[LabTestRead])
def list_lab_tests(
    status: Optional[LabTestStatus] = None,
    material_id: Optional[int] = None,
    roll_id: Optional[int] = None,
    offset: int = 0,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    offset, limit = page_bounds(offset, limit)
    stmt = select(LabTest).order_by(LabTest.id.desc())
    if status is not None:
        stmt = stmt.where(LabTest.status == status)
    if material_id is not None:
        stmt = stmt.where(LabTest.material_id == material_id)
    if roll_id is not None:
        stmt = stmt.where(LabTest.roll_id == roll_id)
    return session.exec(stmt.offset(offset).limit(limit)).all()


@router.post("/lab-tests/{test_id}/complete", response_model=LabTestRead)
def complete_lab_test(
    test_id: int,
    payload: LabTestComplete,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.quality_inspector)),
):
    test = session.get(LabTest, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Lab test not found")
    if test.status != LabTestStatus.pending:
        raise HTTPException(status_code=409, detail="Only pending lab tests can be completed")
    test.status = LabTestStatus.passed if payload.passed else LabTestStatus.failed
    test.measured_value = payload.measured_value
    test.completed_date = payload.completed_date or date.today()
    test.certificate_reference = payload.certificate_reference
    test.tested_by = actor
    if payload.note is not None:
        test.note = payload.note
    if not payload.passed and test.roll_id is not None:
        roll = session.get(Roll, test.roll_id)
        if roll and roll.status not in ("consumed", "rejected"):
            roll.status = "quarantined"
            session.add(roll)
    session.add(test)
    session.commit()
    session.refresh(test)
    return test


@router.get("/defect-analytics", response_model=list[DefectAnalyticsRead])
def defect_analytics(session: Session = Depends(get_session)):
    defects = session.exec(select(FourPointDefect)).all()
    grouped: dict[str, dict[str, int]] = {}
    for defect in defects:
        key = defect.defect_code or defect.category or defect.description or "Unclassified"
        row = grouped.setdefault(key, {"occurrences": 0, "penalty_points": 0})
        row["occurrences"] += 1
        row["penalty_points"] += defect.penalty_points
    total = sum(row["occurrences"] for row in grouped.values())
    return [
        DefectAnalyticsRead(
            defect_key=key,
            occurrences=row["occurrences"],
            penalty_points=row["penalty_points"],
            share_pct=(Decimal(row["occurrences"]) / Decimal(total) * 100).quantize(Decimal("0.01")) if total else Decimal("0"),
        )
        for key, row in sorted(grouped.items(), key=lambda item: item[1]["penalty_points"], reverse=True)
    ]


@router.get("/inspections/export")
def export_inspections(session: Session = Depends(get_session)):
    rows = []
    for inspection in session.exec(select(FourPointInspection)).all():
        rows.append({
            "number": inspection.inspection_number,
            "type": "four_point",
            "reference": inspection.roll_id,
            "result": inspection.result.value,
            "defects": len(inspection.defects),
            "metric": inspection.points_per_100sqyd,
        })
    for inspection in session.exec(select(InlineInspection)).all():
        rows.append({
            "number": inspection.inspection_number,
            "type": "inline",
            "reference": inspection.sewing_order_id,
            "result": "recorded",
            "defects": inspection.defects_found,
            "metric": inspection.dhu,
        })
    for inspection in session.exec(select(FinalInspection)).all():
        rows.append({
            "number": inspection.inspection_number,
            "type": "final",
            "reference": inspection.sales_order_id,
            "result": inspection.result.value,
            "defects": inspection.defects_found,
            "metric": inspection.aql,
        })
    return csv_download(
        "quality-inspections.csv",
        [
            ("number", "Inspection"), ("type", "Type"),
            ("reference", "Reference ID"), ("result", "Result"),
            ("defects", "Defects"), ("metric", "Score / AQL"),
        ],
        rows,
    )
