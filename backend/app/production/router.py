from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..db import get_session
from ..inventory.models import ReserveRequest
from ..inventory.service import StockError, reserve
from ..kernel.idempotency import IdempotencyInProgress, claim, complete
from ..kernel.numbering import next_document_number
from ..kernel.query import csv_download, page_bounds
from ..kernel.rbac import Role, require_roles
from ..styles.models import Style
from .models import (
    CompleteCutRequest,
    CutOrder,
    CutOrderCreate,
    CutOrderRead,
    CutOrderStatus,
    CutSizeRead,
    DailyOutputCreate,
    DailyOutputRead,
    IssueFabricRequest,
    ProductionRoute,
    ProductionRouteCreate,
    ProductionRouteRead,
    ProductionRouteStep,
    RouteStepRead,
    ReserveFabricRequest,
    SewingDailyOutput,
    SewingOrder,
    SewingOrderCreate,
    SewingOrderRead,
    SubcontractCreate,
    SubcontractOrder,
    SubcontractReceiveRequest,
    SubcontractRead,
    SubcontractStatus,
    WipMovement,
    WipMovementCreate,
    WipMovementRead,
    WipStepSummary,
)
from .service import (
    ProductionError,
    add_daily_output,
    average_efficiency,
    complete_cut_order,
    create_cut_order,
    issue_fabric,
    receive_subcontract,
)

router = APIRouter(prefix="/production", tags=["production"])

_OUTPUT_SCOPE = "sewing_daily_output"


def _cut_read(cut_order: CutOrder) -> CutOrderRead:
    return CutOrderRead(
        id=cut_order.id,
        order_number=cut_order.order_number,
        sales_order_id=cut_order.sales_order_id,
        style_id=cut_order.style_id,
        colour_id=cut_order.colour_id,
        bom_version_id=cut_order.bom_version_id,
        fabric_material_id=cut_order.fabric_material_id,
        fabric_required=cut_order.fabric_required,
        fabric_issued=cut_order.fabric_issued,
        pieces_cut=cut_order.pieces_cut,
        status=cut_order.status,
        sizes=[
            CutSizeRead(
                size_label=s.size_label,
                position=s.position,
                planned_qty=s.planned_qty,
                cut_qty=s.cut_qty,
            )
            for s in cut_order.sizes
        ],
    )


# --------------------------------------------------------------------------- #
# Cut orders (5.1 / 5.2 / 5.3)
# --------------------------------------------------------------------------- #
@router.post("/cut-orders", response_model=CutOrderRead, status_code=201)
def create_cut(
    payload: CutOrderCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner, Role.cutting_supervisor)),
):
    try:
        cut_order = create_cut_order(session, payload)
    except ProductionError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(cut_order)
    return _cut_read(cut_order)


@router.get("/cut-orders", response_model=List[CutOrderRead])
def list_cuts(session: Session = Depends(get_session)):
    stmt = select(CutOrder).options(selectinload(CutOrder.sizes)).order_by(CutOrder.id)
    return [_cut_read(c) for c in session.exec(stmt).all()]


@router.get("/cut-orders/{cut_id}", response_model=CutOrderRead)
def get_cut(cut_id: int, session: Session = Depends(get_session)):
    cut_order = session.get(CutOrder, cut_id)
    if cut_order is None:
        raise HTTPException(status_code=404, detail="Cut order not found")
    return _cut_read(cut_order)


@router.post("/cut-orders/{cut_id}/reserve-fabric", response_model=CutOrderRead)
def reserve_fabric(
    cut_id: int,
    payload: ReserveFabricRequest,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner, Role.stores)),
):
    cut_order = session.get(CutOrder, cut_id)
    if cut_order is None:
        raise HTTPException(status_code=404, detail="Cut order not found")
    if cut_order.status != CutOrderStatus.planned:
        raise HTTPException(
            status_code=409, detail=f"Cannot reserve for a '{cut_order.status.value}' cut order"
        )
    try:
        reserve(
            session,
            ReserveRequest(
                material_id=cut_order.fabric_material_id,
                required_qty=cut_order.fabric_required,
                shade_group=payload.shade_group,
                min_width_cm=payload.min_width_cm,
                reference_type="cut_order",
                reference_id=cut_order.id,
            ),
        )
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cut_order.status = CutOrderStatus.fabric_reserved
    session.add(cut_order)
    session.commit()
    session.refresh(cut_order)
    return _cut_read(cut_order)


@router.post("/cut-orders/{cut_id}/issue-fabric", response_model=CutOrderRead)
def issue(
    cut_id: int,
    payload: IssueFabricRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores, Role.cutting_supervisor)),
):
    cut_order = session.get(CutOrder, cut_id)
    if cut_order is None:
        raise HTTPException(status_code=404, detail="Cut order not found")
    try:
        issue_fabric(session, cut_order, payload.allocations, actor)
    except ProductionError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(cut_order)
    return _cut_read(cut_order)


@router.post("/cut-orders/{cut_id}/complete", response_model=CutOrderRead)
def complete_cut(
    cut_id: int,
    payload: CompleteCutRequest,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.cutting_supervisor)),
):
    cut_order = session.get(CutOrder, cut_id)
    if cut_order is None:
        raise HTTPException(status_code=404, detail="Cut order not found")
    try:
        complete_cut_order(session, cut_order, payload.cut_qty)
    except ProductionError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(cut_order)
    return _cut_read(cut_order)


# --------------------------------------------------------------------------- #
# Sewing orders + daily output (5.4 / 5.5)
# --------------------------------------------------------------------------- #
def _sewing_read(sewing: SewingOrder) -> SewingOrderRead:
    return SewingOrderRead(
        id=sewing.id,
        order_number=sewing.order_number,
        cut_order_id=sewing.cut_order_id,
        style_id=sewing.style_id,
        line=sewing.line,
        planned_qty=sewing.planned_qty,
        produced_qty=sewing.produced_qty,
        sam=sewing.sam,
        status=sewing.status,
        average_efficiency_pct=average_efficiency(sewing),
    )


@router.post("/sewing-orders", response_model=SewingOrderRead, status_code=201)
def create_sewing(
    payload: SewingOrderCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner, Role.sewing_supervisor)),
):
    style = session.get(Style, payload.style_id)
    if style is None:
        raise HTTPException(status_code=404, detail="Style not found")
    number = next_document_number(session, "SEWING_ORDER", "SEW")
    sewing = SewingOrder(
        order_number=number,
        cut_order_id=payload.cut_order_id,
        style_id=payload.style_id,
        line=payload.line,
        planned_qty=payload.planned_qty,
        sam=style.standard_sam,
    )
    session.add(sewing)
    session.commit()
    session.refresh(sewing)
    return _sewing_read(sewing)


@router.get("/sewing-orders", response_model=List[SewingOrderRead])
def list_sewing_orders(session: Session = Depends(get_session)):
    orders = session.exec(
        select(SewingOrder)
        .options(selectinload(SewingOrder.daily_outputs))
        .order_by(SewingOrder.id.desc())
    ).all()
    return [_sewing_read(order) for order in orders]


@router.get("/sewing-orders/{sewing_id}", response_model=SewingOrderRead)
def get_sewing(sewing_id: int, session: Session = Depends(get_session)):
    sewing = session.get(SewingOrder, sewing_id)
    if sewing is None:
        raise HTTPException(status_code=404, detail="Sewing order not found")
    return _sewing_read(sewing)


@router.post(
    "/sewing-orders/{sewing_id}/daily-output",
    response_model=DailyOutputRead,
    status_code=201,
)
def record_output(
    sewing_id: int,
    payload: DailyOutputCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.sewing_supervisor)),
):
    sewing = session.get(SewingOrder, sewing_id)
    if sewing is None:
        raise HTTPException(status_code=404, detail="Sewing order not found")

    # Claim before recording output so concurrent client retries are safe.
    idempotency_key = None
    if payload.client_key:
        try:
            idempotency_key = claim(session, _OUTPUT_SCOPE, payload.client_key)
        except IdempotencyInProgress as exc:
            raise HTTPException(status_code=409, detail="Idempotent request is in progress") from exc
        if idempotency_key.resource_id is not None:
            output = session.get(SewingDailyOutput, idempotency_key.resource_id)
            return output

    try:
        output = add_daily_output(session, sewing, payload)
        if idempotency_key is not None:
            complete(session, idempotency_key, output.id)
    except ProductionError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(output)
    return output


@router.get(
    "/sewing-orders/{sewing_id}/daily-outputs",
    response_model=List[DailyOutputRead],
)
def list_daily_outputs(sewing_id: int, session: Session = Depends(get_session)):
    if session.get(SewingOrder, sewing_id) is None:
        raise HTTPException(status_code=404, detail="Sewing order not found")
    return session.exec(
        select(SewingDailyOutput)
        .where(SewingDailyOutput.sewing_order_id == sewing_id)
        .order_by(SewingDailyOutput.output_date.desc(), SewingDailyOutput.id.desc())
    ).all()


# --------------------------------------------------------------------------- #
# Subcontracting (5.6)
# --------------------------------------------------------------------------- #
def _sub_read(order: SubcontractOrder) -> SubcontractRead:
    return SubcontractRead(
        id=order.id,
        order_number=order.order_number,
        subcontractor_id=order.subcontractor_id,
        process=order.process,
        cut_order_id=order.cut_order_id,
        sent_qty=order.sent_qty,
        received_qty=order.received_qty,
        outstanding_qty=order.sent_qty - order.received_qty,
        rate=order.rate,
        status=order.status,
    )


@router.post("/subcontract-orders", response_model=SubcontractRead, status_code=201)
def create_subcontract(
    payload: SubcontractCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner, Role.procurement)),
):
    if payload.sent_qty <= 0:
        raise HTTPException(status_code=422, detail="Sent quantity must be positive")
    number = next_document_number(session, "SUBCONTRACT", "SC")
    order = SubcontractOrder(
        order_number=number,
        subcontractor_id=payload.subcontractor_id,
        process=payload.process,
        cut_order_id=payload.cut_order_id,
        sent_qty=payload.sent_qty,
        rate=payload.rate,
        sent_date=payload.sent_date,
        expected_date=payload.expected_date,
        status=SubcontractStatus.open,
    )
    session.add(order)
    session.commit()
    session.refresh(order)
    return _sub_read(order)


@router.get("/subcontract-orders", response_model=List[SubcontractRead])
def list_subcontracts(session: Session = Depends(get_session)):
    orders = session.exec(
        select(SubcontractOrder).order_by(SubcontractOrder.id.desc())
    ).all()
    return [_sub_read(order) for order in orders]


@router.get("/subcontract-orders/{sub_id}", response_model=SubcontractRead)
def get_subcontract(sub_id: int, session: Session = Depends(get_session)):
    order = session.get(SubcontractOrder, sub_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Subcontract order not found")
    return _sub_read(order)


@router.post("/subcontract-orders/{sub_id}/receive", response_model=SubcontractRead)
def receive_sub(
    sub_id: int,
    payload: SubcontractReceiveRequest,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.stores, Role.planner)),
):
    order = session.get(SubcontractOrder, sub_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Subcontract order not found")
    try:
        receive_subcontract(session, order, payload.received_qty)
    except ProductionError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(order)
    return _sub_read(order)


# --------------------------------------------------------------------------- #
# Production routing and WIP tracking
# --------------------------------------------------------------------------- #
def _route_read(session: Session, route: ProductionRoute) -> ProductionRouteRead:
    steps = session.exec(
        select(ProductionRouteStep)
        .where(ProductionRouteStep.route_id == route.id)
        .order_by(ProductionRouteStep.sequence)
    ).all()
    return ProductionRouteRead(
        id=route.id,
        route_number=route.route_number,
        style_id=route.style_id,
        name=route.name,
        version_no=route.version_no,
        active=route.active,
        steps=[RouteStepRead.model_validate(step) for step in steps],
    )


@router.post("/routes", response_model=ProductionRouteRead, status_code=201)
def create_route(
    payload: ProductionRouteCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner)),
):
    if session.get(Style, payload.style_id) is None:
        raise HTTPException(status_code=404, detail="Style not found")
    if not payload.steps:
        raise HTTPException(status_code=422, detail="A route needs at least one step")
    sequences = [step.sequence for step in payload.steps]
    if any(sequence < 1 for sequence in sequences) or len(set(sequences)) != len(sequences):
        raise HTTPException(status_code=422, detail="Step sequences must be positive and unique")
    version = session.exec(
        select(func.coalesce(func.max(ProductionRoute.version_no), 0)).where(
            ProductionRoute.style_id == payload.style_id
        )
    ).one() + 1
    for current in session.exec(
        select(ProductionRoute).where(
            ProductionRoute.style_id == payload.style_id,
            ProductionRoute.active == True,  # noqa: E712
        )
    ).all():
        current.active = False
        session.add(current)
    route = ProductionRoute(
        route_number=next_document_number(session, "PRODUCTION_ROUTE", "RT"),
        style_id=payload.style_id,
        name=payload.name,
        version_no=version,
        active=True,
    )
    session.add(route)
    session.flush()
    for step in sorted(payload.steps, key=lambda item: item.sequence):
        session.add(ProductionRouteStep(route_id=route.id, **step.model_dump()))
    session.commit()
    session.refresh(route)
    return _route_read(session, route)


@router.get("/routes", response_model=List[ProductionRouteRead])
def list_routes(
    style_id: Optional[int] = None,
    active: Optional[bool] = None,
    offset: int = 0,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    offset, limit = page_bounds(offset, limit)
    stmt = select(ProductionRoute).order_by(ProductionRoute.id.desc())
    if style_id is not None:
        stmt = stmt.where(ProductionRoute.style_id == style_id)
    if active is not None:
        stmt = stmt.where(ProductionRoute.active == active)
    return [_route_read(session, route) for route in session.exec(stmt.offset(offset).limit(limit)).all()]


def _step_balance(session: Session, sewing_order_id: int, route_step_id: int) -> tuple[int, int, int, int]:
    movements = session.exec(
        select(WipMovement).where(
            WipMovement.sewing_order_id == sewing_order_id,
            WipMovement.route_step_id == route_step_id,
        )
    ).all()
    quantity_in = sum(movement.quantity_in for movement in movements)
    quantity_out = sum(movement.quantity_out for movement in movements)
    rejected = sum(movement.rejected_qty for movement in movements)
    return quantity_in, quantity_out, rejected, quantity_in - quantity_out - rejected


@router.post(
    "/sewing-orders/{sewing_id}/wip",
    response_model=WipMovementRead,
    status_code=201,
)
def record_wip(
    sewing_id: int,
    payload: WipMovementCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.sewing_supervisor, Role.planner)),
):
    sewing = session.get(SewingOrder, sewing_id)
    if sewing is None:
        raise HTTPException(status_code=404, detail="Sewing order not found")
    step = session.get(ProductionRouteStep, payload.route_step_id)
    if step is None:
        raise HTTPException(status_code=404, detail="Route step not found")
    route = session.get(ProductionRoute, step.route_id)
    if route is None or route.style_id != sewing.style_id:
        raise HTTPException(status_code=422, detail="Route step does not belong to the sewing order style")
    if min(payload.quantity_in, payload.quantity_out, payload.rejected_qty) < 0:
        raise HTTPException(status_code=422, detail="WIP quantities cannot be negative")
    if payload.quantity_in + payload.quantity_out + payload.rejected_qty <= 0:
        raise HTTPException(status_code=422, detail="At least one WIP quantity is required")
    _, _, _, current = _step_balance(session, sewing_id, step.id)
    if payload.quantity_out + payload.rejected_qty > current + payload.quantity_in:
        raise HTTPException(status_code=422, detail=f"Step has only {current} units in WIP")
    movement = WipMovement(
        movement_number=next_document_number(session, "WIP_MOVEMENT", "WIP"),
        sewing_order_id=sewing_id,
        route_step_id=step.id,
        quantity_in=payload.quantity_in,
        quantity_out=payload.quantity_out,
        rejected_qty=payload.rejected_qty,
        recorded_by=actor,
        note=payload.note,
    )
    session.add(movement)
    session.commit()
    session.refresh(movement)
    return movement


@router.get("/sewing-orders/{sewing_id}/wip", response_model=List[WipStepSummary])
def wip_summary(sewing_id: int, session: Session = Depends(get_session)):
    sewing = session.get(SewingOrder, sewing_id)
    if sewing is None:
        raise HTTPException(status_code=404, detail="Sewing order not found")
    route = session.exec(
        select(ProductionRoute).where(
            ProductionRoute.style_id == sewing.style_id,
            ProductionRoute.active == True,  # noqa: E712
        )
    ).first()
    if route is None:
        return []
    steps = session.exec(
        select(ProductionRouteStep)
        .where(ProductionRouteStep.route_id == route.id)
        .order_by(ProductionRouteStep.sequence)
    ).all()
    output = []
    for step in steps:
        quantity_in, quantity_out, rejected, wip = _step_balance(session, sewing_id, step.id)
        output.append(WipStepSummary(
            route_step_id=step.id,
            sequence=step.sequence,
            operation=step.operation,
            work_center=step.work_center,
            quantity_in=quantity_in,
            quantity_out=quantity_out,
            rejected_qty=rejected,
            wip_qty=wip,
        ))
    return output


@router.get("/wip/export")
def export_wip(session: Session = Depends(get_session)):
    movements = session.exec(select(WipMovement).order_by(WipMovement.id.desc())).all()
    return csv_download(
        "production-wip.csv",
        [
            ("number", "Movement"), ("sewing", "Sewing Order ID"),
            ("step", "Route Step ID"), ("in", "Quantity In"),
            ("out", "Quantity Out"), ("rejected", "Rejected"),
            ("recorded", "Recorded At"), ("actor", "Recorded By"),
        ],
        [
            {
                "number": movement.movement_number,
                "sewing": movement.sewing_order_id,
                "step": movement.route_step_id,
                "in": movement.quantity_in,
                "out": movement.quantity_out,
                "rejected": movement.rejected_qty,
                "recorded": movement.recorded_at,
                "actor": movement.recorded_by,
            }
            for movement in movements
        ],
    )
