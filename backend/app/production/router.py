from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..inventory.models import ReserveRequest
from ..inventory.service import StockError, reserve
from ..kernel.idempotency import find_existing, record
from ..kernel.numbering import next_document_number
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
    return [_cut_read(c) for c in session.exec(select(CutOrder).order_by(CutOrder.id)).all()]


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

    # Idempotent shop-floor capture.
    if payload.client_key:
        existing = find_existing(session, _OUTPUT_SCOPE, payload.client_key)
        if existing is not None:
            output = session.get(SewingDailyOutput, existing)
            return output

    try:
        output = add_daily_output(session, sewing, payload)
        if payload.client_key:
            record(session, _OUTPUT_SCOPE, payload.client_key, output.id)
    except ProductionError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(output)
    return output


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
