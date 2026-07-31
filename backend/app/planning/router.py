from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.query import csv_download, page_bounds
from ..kernel.rbac import Role, require_roles
from ..kernel.types import quantize_qty
from ..masters.models import Material
from ..production.models import SewingOrder
from ..styles.models import Style
from .capacity import available_to_promise, capacity_board
from .models import (
    AtpRequest,
    AtpResponse,
    CapacityBoardRead,
    CapacityBooking,
    CapacityBookingCreate,
    CapacityBookingRead,
    CapacityBookingStatus,
    CapacityException,
    CapacityExceptionCreate,
    CapacityExceptionRead,
    FirmRequest,
    MrpBucket,
    MrpBucketRead,
    MrpDemandSource,
    MrpDemandSourceRead,
    MrpRun,
    MrpRunCreate,
    MrpRunDetailRead,
    MrpRunRead,
    PlannedOrder,
    PlannedOrderRead,
    PlannedOrderStatus,
    WorkCentre,
    WorkCentreCreate,
    WorkCentreRead,
    WorkCentreType,
    WorkCentreUpdate,
)
from .mrp import PlanningError, firm_planned_orders, run_mrp

router = APIRouter(prefix="/planning", tags=["planning"])


# --------------------------------------------------------------------------- #
# Work centres
# --------------------------------------------------------------------------- #
def _centre_read(centre: WorkCentre) -> WorkCentreRead:
    return WorkCentreRead(
        id=centre.id,
        code=centre.code,
        name=centre.name,
        centre_type=centre.centre_type,
        operators=centre.operators,
        shift_minutes=centre.shift_minutes,
        shifts_per_day=centre.shifts_per_day,
        efficiency_pct=centre.efficiency_pct,
        daily_minutes=quantize_qty(centre.daily_minutes),
        active=centre.active,
    )


@router.post("/work-centres", response_model=WorkCentreRead, status_code=201)
def create_work_centre(
    payload: WorkCentreCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner)),
):
    code = payload.code.strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="Work centre code is required")
    if session.exec(select(WorkCentre).where(WorkCentre.code == code)).first():
        raise HTTPException(status_code=409, detail="Work centre code already exists")
    centre = WorkCentre(**{**payload.model_dump(), "code": code})
    session.add(centre)
    session.commit()
    session.refresh(centre)
    return _centre_read(centre)


@router.get("/work-centres", response_model=List[WorkCentreRead])
def list_work_centres(
    session: Session = Depends(get_session),
    centre_type: Optional[WorkCentreType] = None,
    active_only: bool = True,
):
    stmt = select(WorkCentre)
    if centre_type is not None:
        stmt = stmt.where(WorkCentre.centre_type == centre_type)
    if active_only:
        stmt = stmt.where(WorkCentre.active == True)  # noqa: E712
    return [_centre_read(c) for c in session.exec(stmt.order_by(WorkCentre.code)).all()]


@router.patch("/work-centres/{centre_id}", response_model=WorkCentreRead)
def update_work_centre(
    centre_id: int,
    payload: WorkCentreUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner)),
):
    centre = session.get(WorkCentre, centre_id)
    if centre is None:
        raise HTTPException(status_code=404, detail="Work centre not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(centre, field, value)
    session.add(centre)
    session.commit()
    session.refresh(centre)
    return _centre_read(centre)


# --------------------------------------------------------------------------- #
# Capacity exceptions and bookings
# --------------------------------------------------------------------------- #
@router.post("/capacity-exceptions", response_model=CapacityExceptionRead, status_code=201)
def create_capacity_exception(
    payload: CapacityExceptionCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner)),
):
    if session.get(WorkCentre, payload.work_centre_id) is None:
        raise HTTPException(status_code=404, detail="Work centre not found")
    if payload.available_minutes < 0:
        raise HTTPException(status_code=422, detail="Available minutes cannot be negative")
    existing = session.exec(
        select(CapacityException).where(
            CapacityException.work_centre_id == payload.work_centre_id,
            CapacityException.exception_date == payload.exception_date,
        )
    ).first()
    if existing is not None:
        # One row per centre per day: overwrite rather than accumulate, so the
        # override stays unambiguous.
        existing.available_minutes = payload.available_minutes
        existing.reason = payload.reason
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    row = CapacityException(**payload.model_dump())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.get("/capacity-exceptions", response_model=List[CapacityExceptionRead])
def list_capacity_exceptions(
    session: Session = Depends(get_session),
    work_centre_id: Optional[int] = None,
):
    stmt = select(CapacityException)
    if work_centre_id is not None:
        stmt = stmt.where(CapacityException.work_centre_id == work_centre_id)
    return session.exec(stmt.order_by(CapacityException.exception_date)).all()


@router.post("/capacity-bookings", response_model=CapacityBookingRead, status_code=201)
def create_capacity_booking(
    payload: CapacityBookingCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner)),
):
    """Reserve minutes on a work centre.

    When the booking references a sewing order and no minutes are given, the
    load is derived from that order's own SAM × planned quantity — the planner
    should not have to restate a number the system already knows.
    """
    centre = session.get(WorkCentre, payload.work_centre_id)
    if centre is None:
        raise HTTPException(status_code=404, detail="Work centre not found")
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="End date cannot precede start date")

    minutes = payload.minutes
    if minutes is None and payload.sewing_order_id is not None:
        order = session.get(SewingOrder, payload.sewing_order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Sewing order not found")
        sam = order.sam
        if sam is None:
            style = session.get(Style, order.style_id)
            sam = style.standard_sam if style else None
        if sam is None:
            raise HTTPException(
                status_code=422,
                detail="Cannot derive minutes: neither the sewing order nor its style has a SAM",
            )
        minutes = quantize_qty(Decimal(sam) * Decimal(order.planned_qty))
    if minutes is None:
        raise HTTPException(status_code=422, detail="Minutes are required")
    if minutes <= 0:
        raise HTTPException(status_code=422, detail="Minutes must be positive")

    booking = CapacityBooking(
        work_centre_id=payload.work_centre_id,
        sewing_order_id=payload.sewing_order_id,
        cut_order_id=payload.cut_order_id,
        sales_order_id=payload.sales_order_id,
        description=payload.description,
        start_date=payload.start_date,
        end_date=payload.end_date,
        minutes=minutes,
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


@router.get("/capacity-bookings", response_model=List[CapacityBookingRead])
def list_capacity_bookings(
    session: Session = Depends(get_session),
    work_centre_id: Optional[int] = None,
    status: Optional[CapacityBookingStatus] = None,
    offset: int = 0,
    limit: int = Query(default=100),
):
    offset, limit = page_bounds(offset, limit)
    stmt = select(CapacityBooking)
    if work_centre_id is not None:
        stmt = stmt.where(CapacityBooking.work_centre_id == work_centre_id)
    if status is not None:
        stmt = stmt.where(CapacityBooking.status == status)
    return session.exec(
        stmt.order_by(CapacityBooking.start_date).offset(offset).limit(limit)
    ).all()


@router.post(
    "/capacity-bookings/{booking_id}/cancel", response_model=CapacityBookingRead
)
def cancel_capacity_booking(
    booking_id: int,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner)),
):
    booking = session.get(CapacityBooking, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    booking.status = CapacityBookingStatus.cancelled
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


# --------------------------------------------------------------------------- #
# Capacity board
# --------------------------------------------------------------------------- #
@router.get("/capacity-board", response_model=CapacityBoardRead)
def get_capacity_board(
    session: Session = Depends(get_session),
    horizon_start: Optional[date] = None,
    horizon_end: Optional[date] = None,
    bucket_days: int = 7,
    centre_type: Optional[WorkCentreType] = None,
):
    start = horizon_start or date.today()
    end = horizon_end or (start + timedelta(days=56))
    try:
        return capacity_board(session, start, end, bucket_days, centre_type)
    except PlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# MRP
# --------------------------------------------------------------------------- #
def _run_summary(session: Session, run: MrpRun) -> dict:
    materials = session.exec(
        select(MrpBucket.material_id).where(MrpBucket.run_id == run.id).distinct()
    ).all()
    planned = session.exec(
        select(PlannedOrder).where(PlannedOrder.run_id == run.id)
    ).all()
    return {
        "id": run.id,
        "run_number": run.run_number,
        "horizon_start": run.horizon_start,
        "horizon_end": run.horizon_end,
        "bucket_days": run.bucket_days,
        "status": run.status,
        "generated_by": run.generated_by,
        "notes": run.notes,
        "material_count": len(materials),
        "planned_order_count": len(planned),
        "past_due_count": sum(1 for p in planned if p.past_due),
    }


def _materials_by_id(session: Session, ids) -> dict:
    """Batch-load materials for a read model.

    Read models used to call ``session.get`` per row. That is one network round
    trip per row against a remote database — the single largest cost in these
    endpoints. One ``IN`` query replaces all of them.
    """
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    rows = session.exec(select(Material).where(Material.id.in_(ids))).all()
    return {m.id: m for m in rows}


def _planned_read(
    session: Session, planned: PlannedOrder, materials: Optional[dict] = None
) -> PlannedOrderRead:
    material = (
        materials.get(planned.material_id)
        if materials is not None
        else session.get(Material, planned.material_id)
    )
    return PlannedOrderRead(
        id=planned.id,
        run_id=planned.run_id,
        material_id=planned.material_id,
        material_code=material.code if material else None,
        material_name=material.name if material else None,
        quantity=planned.quantity,
        need_date=planned.need_date,
        release_date=planned.release_date,
        lead_time_days=planned.lead_time_days,
        status=planned.status,
        requisition_id=planned.requisition_id,
        past_due=planned.past_due,
    )


@router.post("/mrp-runs", response_model=MrpRunRead, status_code=201)
def create_mrp_run(
    payload: MrpRunCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.planner, Role.procurement)),
):
    try:
        run = run_mrp(session, payload, actor)
    except PlanningError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(run)
    return MrpRunRead(**_run_summary(session, run))


@router.get("/mrp-runs", response_model=List[MrpRunRead])
def list_mrp_runs(session: Session = Depends(get_session), limit: int = Query(default=25)):
    _, limit = page_bounds(0, limit)
    runs = session.exec(select(MrpRun).order_by(MrpRun.id.desc()).limit(limit)).all()
    return [MrpRunRead(**_run_summary(session, r)) for r in runs]


@router.get("/mrp-runs/{run_id}", response_model=MrpRunDetailRead)
def get_mrp_run(
    run_id: int,
    session: Session = Depends(get_session),
    material_id: Optional[int] = None,
    shortages_only: bool = False,
):
    run = session.get(MrpRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="MRP run not found")

    stmt = select(MrpBucket).where(MrpBucket.run_id == run_id)
    if material_id is not None:
        stmt = stmt.where(MrpBucket.material_id == material_id)
    if shortages_only:
        stmt = stmt.where(MrpBucket.net_requirement > 0)
    buckets = session.exec(
        stmt.order_by(MrpBucket.material_id, MrpBucket.sequence)
    ).all()

    # Two batch loads instead of two queries per bucket. A 90-day horizon over a
    # dozen materials is ~200 buckets, so this is the difference between one
    # round trip and two hundred.
    materials = _materials_by_id(session, (b.material_id for b in buckets))
    sources_by_bucket: dict = {}
    bucket_ids = [b.id for b in buckets]
    if bucket_ids:
        for source in session.exec(
            select(MrpDemandSource).where(MrpDemandSource.bucket_id.in_(bucket_ids))
        ).all():
            sources_by_bucket.setdefault(source.bucket_id, []).append(source)

    bucket_reads: List[MrpBucketRead] = []
    for bucket in buckets:
        material = materials.get(bucket.material_id)
        sources = sources_by_bucket.get(bucket.id, [])
        bucket_reads.append(
            MrpBucketRead(
                id=bucket.id,
                material_id=bucket.material_id,
                material_code=material.code if material else None,
                material_name=material.name if material else None,
                sequence=bucket.sequence,
                bucket_start=bucket.bucket_start,
                bucket_end=bucket.bucket_end,
                opening_balance=bucket.opening_balance,
                gross_requirement=bucket.gross_requirement,
                scheduled_receipts=bucket.scheduled_receipts,
                net_requirement=bucket.net_requirement,
                planned_order_qty=bucket.planned_order_qty,
                projected_available=bucket.projected_available,
                demand_sources=[
                    MrpDemandSourceRead(
                        sales_order_id=s.sales_order_id,
                        quantity=s.quantity,
                        need_date=s.need_date,
                    )
                    for s in sources
                ],
            )
        )

    planned = session.exec(
        select(PlannedOrder)
        .where(PlannedOrder.run_id == run_id)
        .order_by(PlannedOrder.release_date, PlannedOrder.material_id)
    ).all()

    planned_materials = _materials_by_id(session, (p.material_id for p in planned))
    return MrpRunDetailRead(
        **_run_summary(session, run),
        buckets=bucket_reads,
        planned_orders=[_planned_read(session, p, planned_materials) for p in planned],
    )


@router.get("/mrp-runs/{run_id}/planned-orders", response_model=List[PlannedOrderRead])
def list_planned_orders(
    run_id: int,
    session: Session = Depends(get_session),
    status: Optional[PlannedOrderStatus] = None,
    past_due_only: bool = False,
):
    stmt = select(PlannedOrder).where(PlannedOrder.run_id == run_id)
    if status is not None:
        stmt = stmt.where(PlannedOrder.status == status)
    if past_due_only:
        stmt = stmt.where(PlannedOrder.past_due == True)  # noqa: E712
    rows = session.exec(stmt.order_by(PlannedOrder.release_date)).all()
    materials = _materials_by_id(session, (p.material_id for p in rows))
    return [_planned_read(session, p, materials) for p in rows]


@router.post("/mrp-runs/{run_id}/firm", status_code=201)
def firm_run(
    run_id: int,
    payload: FirmRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.planner, Role.procurement)),
):
    """Convert planned orders into a purchase requisition."""
    run = session.get(MrpRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="MRP run not found")
    try:
        requisition = firm_planned_orders(session, run, payload.planned_order_ids, actor)
    except PlanningError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(requisition)
    return {
        "requisition_id": requisition.id,
        "requisition_number": requisition.requisition_number,
        "run_status": run.status,
    }


@router.get("/mrp-runs/{run_id}/export")
def export_mrp_run(run_id: int, session: Session = Depends(get_session)):
    run = session.get(MrpRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="MRP run not found")
    buckets = session.exec(
        select(MrpBucket)
        .where(MrpBucket.run_id == run_id)
        .order_by(MrpBucket.material_id, MrpBucket.sequence)
    ).all()
    rows = []
    for bucket in buckets:
        material = session.get(Material, bucket.material_id)
        rows.append({
            "material_code": material.code if material else "",
            "material_name": material.name if material else "",
            "bucket_start": bucket.bucket_start,
            "bucket_end": bucket.bucket_end,
            "opening_balance": bucket.opening_balance,
            "gross_requirement": bucket.gross_requirement,
            "scheduled_receipts": bucket.scheduled_receipts,
            "net_requirement": bucket.net_requirement,
            "planned_order_qty": bucket.planned_order_qty,
            "projected_available": bucket.projected_available,
        })
    return csv_download(
        f"mrp-{run.run_number}.csv",
        columns=[
            ("material_code", "Material"),
            ("material_name", "Description"),
            ("bucket_start", "From"),
            ("bucket_end", "To"),
            ("opening_balance", "Opening"),
            ("gross_requirement", "Gross requirement"),
            ("scheduled_receipts", "Scheduled receipts"),
            ("net_requirement", "Net requirement"),
            ("planned_order_qty", "Planned order"),
            ("projected_available", "Projected available"),
        ],
        rows=rows,
    )


# --------------------------------------------------------------------------- #
# Available to promise
# --------------------------------------------------------------------------- #
@router.post("/atp", response_model=AtpResponse)
def check_atp(payload: AtpRequest, session: Session = Depends(get_session)):
    """Can this quantity of this style be promised by this date?"""
    try:
        return available_to_promise(session, payload)
    except PlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
