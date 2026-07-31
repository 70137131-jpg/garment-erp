from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.query import csv_download, page_bounds
from ..kernel.rbac import Role, require_roles
from ..kernel.types import quantize_qty
from ..production.models import CutOrder
from .models import (
    CutPlan,
    CutPlanCreate,
    CutPlanDemand,
    CutPlanDemandRead,
    CutPlanLay,
    CutPlanLayRead,
    CutPlanRead,
    CutPlanStatus,
    Marker,
    MarkerCreate,
    MarkerRead,
    MarkerSizeRead,
    MarkerStatus,
)
from .service import MarkerError, build_cut_plan, create_marker

router = APIRouter(prefix="/marker", tags=["marker"])

_ZERO = Decimal("0")


def _cm_to_m(value: Optional[Decimal]) -> Optional[Decimal]:
    if value is None:
        return None
    return quantize_qty(Decimal(value) / Decimal("100"))


def _marker_read(marker: Marker) -> MarkerRead:
    return MarkerRead(
        id=marker.id,
        marker_code=marker.marker_code,
        style_id=marker.style_id,
        bom_version_id=marker.bom_version_id,
        width_cm=marker.width_cm,
        length_cm=marker.length_cm,
        pattern_area_cm2=marker.pattern_area_cm2,
        efficiency_pct=marker.efficiency_pct,
        max_plies=marker.max_plies,
        status=marker.status,
        notes=marker.notes,
        pieces_per_ply=sum(s.quantity for s in marker.sizes),
        sizes=[
            MarkerSizeRead(size_label=s.size_label, quantity=s.quantity)
            for s in sorted(marker.sizes, key=lambda x: x.size_label)
        ],
    )


@router.post("/markers", response_model=MarkerRead, status_code=201)
def create(
    payload: MarkerCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.cutting_supervisor, Role.planner, Role.merchandiser)),
):
    try:
        marker = create_marker(session, payload)
    except MarkerError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(marker)
    return _marker_read(marker)


@router.get("/markers", response_model=List[MarkerRead])
def list_markers(
    session: Session = Depends(get_session),
    style_id: Optional[int] = None,
    width_cm: Optional[Decimal] = None,
    status: Optional[MarkerStatus] = None,
):
    stmt = select(Marker)
    if style_id is not None:
        stmt = stmt.where(Marker.style_id == style_id)
    if width_cm is not None:
        stmt = stmt.where(Marker.width_cm == quantize_qty(width_cm))
    if status is not None:
        stmt = stmt.where(Marker.status == status)
    return [_marker_read(m) for m in session.exec(stmt.order_by(Marker.marker_code)).all()]


@router.post("/markers/{marker_id}/approve", response_model=MarkerRead)
def approve_marker(
    marker_id: int,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.cutting_supervisor, Role.planner)),
):
    marker = session.get(Marker, marker_id)
    if marker is None:
        raise HTTPException(status_code=404, detail="Marker not found")
    if marker.status != MarkerStatus.draft:
        raise HTTPException(
            status_code=409, detail=f"Cannot approve a '{marker.status.value}' marker"
        )
    marker.status = MarkerStatus.approved
    session.add(marker)
    session.commit()
    session.refresh(marker)
    return _marker_read(marker)


# --------------------------------------------------------------------------- #
# Cut plans
# --------------------------------------------------------------------------- #
def _plan_read(session: Session, plan: CutPlan) -> CutPlanRead:
    # One query for every marker referenced by the plan's lays, rather than one
    # per lay.
    marker_ids = {l.marker_id for l in plan.lays}
    markers = (
        {m.id: m for m in session.exec(select(Marker).where(Marker.id.in_(marker_ids))).all()}
        if marker_ids else {}
    )
    lays: List[CutPlanLayRead] = []
    for lay in plan.lays:
        marker = markers.get(lay.marker_id)
        lays.append(
            CutPlanLayRead(
                id=lay.id,
                sequence=lay.sequence,
                marker_id=lay.marker_id,
                marker_code=marker.marker_code if marker else None,
                plies=lay.plies,
                fabric_cm=lay.fabric_cm,
                fabric_m=_cm_to_m(lay.fabric_cm) or _ZERO,
                pieces=lay.pieces,
                efficiency_pct=marker.efficiency_pct if marker else _ZERO,
            )
        )

    total_m = _cm_to_m(plan.total_fabric_cm) or _ZERO
    bom_m = _cm_to_m(plan.bom_fabric_cm)
    overcut_pct = (
        quantize_qty(Decimal(plan.overcut_pieces) / Decimal(plan.required_pieces) * 100)
        if plan.required_pieces
        else _ZERO
    )
    return CutPlanRead(
        id=plan.id,
        plan_number=plan.plan_number,
        style_id=plan.style_id,
        sales_order_id=plan.sales_order_id,
        cut_order_id=plan.cut_order_id,
        fabric_material_id=plan.fabric_material_id,
        width_cm=plan.width_cm,
        max_plies=plan.max_plies,
        status=plan.status,
        total_fabric_cm=plan.total_fabric_cm,
        total_fabric_m=total_m,
        total_plies=plan.total_plies,
        lay_count=plan.lay_count,
        required_pieces=plan.required_pieces,
        planned_pieces=plan.planned_pieces,
        overcut_pieces=plan.overcut_pieces,
        overcut_pct=overcut_pct,
        weighted_efficiency_pct=plan.weighted_efficiency_pct,
        bom_fabric_cm=plan.bom_fabric_cm,
        bom_fabric_m=bom_m,
        saving_vs_bom_m=(quantize_qty(bom_m - total_m) if bom_m is not None else None),
        algorithm=plan.algorithm,
        notes=plan.notes,
        lays=lays,
        demands=[
            CutPlanDemandRead(
                size_label=d.size_label,
                required_qty=d.required_qty,
                planned_qty=d.planned_qty,
                overcut_qty=d.overcut_qty,
            )
            for d in sorted(plan.demands, key=lambda x: x.size_label)
        ],
    )


@router.post("/cut-plans", response_model=CutPlanRead, status_code=201)
def create_cut_plan(
    payload: CutPlanCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.cutting_supervisor, Role.planner)),
):
    """Solve a lay plan for a style at a given fabric width."""
    try:
        plan = build_cut_plan(session, payload)
    except MarkerError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(plan)
    return _plan_read(session, plan)


@router.get("/cut-plans", response_model=List[CutPlanRead])
def list_cut_plans(
    session: Session = Depends(get_session),
    style_id: Optional[int] = None,
    sales_order_id: Optional[int] = None,
    offset: int = 0,
    limit: int = Query(default=50),
):
    offset, limit = page_bounds(offset, limit)
    stmt = select(CutPlan)
    if style_id is not None:
        stmt = stmt.where(CutPlan.style_id == style_id)
    if sales_order_id is not None:
        stmt = stmt.where(CutPlan.sales_order_id == sales_order_id)
    plans = session.exec(
        stmt.order_by(CutPlan.id.desc()).offset(offset).limit(limit)
    ).all()
    return [_plan_read(session, p) for p in plans]


@router.get("/cut-plans/{plan_id}", response_model=CutPlanRead)
def get_cut_plan(plan_id: int, session: Session = Depends(get_session)):
    plan = session.get(CutPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Cut plan not found")
    return _plan_read(session, plan)


@router.post("/cut-plans/{plan_id}/approve", response_model=CutPlanRead)
def approve_cut_plan(
    plan_id: int,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.cutting_supervisor, Role.planner)),
):
    """Approve the plan and stamp its efficiency onto the linked cut order.

    This is what turns ``CutOrder.marker_efficiency`` from a typed-in claim into
    a figure derived from the marker geometry actually being spread.
    """
    plan = session.get(CutPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Cut plan not found")
    if plan.status != CutPlanStatus.draft:
        raise HTTPException(
            status_code=409, detail=f"Cannot approve a '{plan.status.value}' plan"
        )
    plan.status = CutPlanStatus.approved
    session.add(plan)

    if plan.cut_order_id is not None:
        cut_order = session.get(CutOrder, plan.cut_order_id)
        if cut_order is not None:
            cut_order.marker_efficiency = plan.weighted_efficiency_pct
            session.add(cut_order)

    session.commit()
    session.refresh(plan)
    return _plan_read(session, plan)


@router.get("/cut-plans/{plan_id}/export")
def export_cut_plan(plan_id: int, session: Session = Depends(get_session)):
    plan = session.get(CutPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Cut plan not found")
    marker_ids = {l.marker_id for l in plan.lays}
    markers = (
        {m.id: m for m in session.exec(select(Marker).where(Marker.id.in_(marker_ids))).all()}
        if marker_ids else {}
    )
    rows = []
    for lay in sorted(plan.lays, key=lambda l: l.sequence):
        marker = markers.get(lay.marker_id)
        rows.append({
            "sequence": lay.sequence,
            "marker": marker.marker_code if marker else "",
            "plies": lay.plies,
            "marker_length_cm": marker.length_cm if marker else "",
            "fabric_m": _cm_to_m(lay.fabric_cm),
            "pieces": lay.pieces,
            "efficiency_pct": marker.efficiency_pct if marker else "",
        })
    return csv_download(
        f"cut-plan-{plan.plan_number}.csv",
        columns=[
            ("sequence", "Lay"), ("marker", "Marker"), ("plies", "Plies"),
            ("marker_length_cm", "Marker length (cm)"), ("fabric_m", "Fabric (m)"),
            ("pieces", "Pieces"), ("efficiency_pct", "Marker efficiency %"),
        ],
        rows=rows,
    )
