from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.numbering import next_document_number
from ..kernel.query import csv_download, page_bounds
from ..kernel.rbac import Role, require_roles
from ..kernel.state_machine import InvalidTransition
from .models import (
    SalesOrder,
    CommercialCheckRead,
    SalesOrderAmendRequest,
    SalesOrderCancelRequest,
    SalesOrderCloseRequest,
    SalesOrderCreate,
    SalesOrderLineRead,
    SalesOrderMaterialRequirement,
    MaterialRequirementRead,
    SalesOrderRead,
    SalesOrderRevision,
    SalesOrderRevisionRead,
    SalesOrderStatus,
    Shipment,
    ShipmentCreate,
    ShipmentLine,
    ShipmentLineRead,
    ShipmentRead,
    SizeCellRead,
    SalesOrderSizeCell,
)
from ..quality.service import order_has_passed_final
from .service import (
    SalesError,
    amend_order,
    build_order,
    close_order,
    commercial_checks,
    confirm_order,
    order_value,
    record_revision,
    recalculate_material_requirements,
    sales_order_state_machine,
    ship_order,
)

router = APIRouter(prefix="/sales-orders", tags=["sales"])


def _serialize(order: SalesOrder) -> SalesOrderRead:
    lines = []
    for line in order.lines:
        qty = sum(c.ordered_qty for c in line.cells)
        lines.append(
            SalesOrderLineRead(
                id=line.id,
                style_id=line.style_id,
                colour_id=line.colour_id,
                unit_price=line.unit_price,
                delivery_date=line.delivery_date,
                destination=line.destination,
                packing_ratio=line.packing_ratio,
                line_quantity=qty,
                line_value=(line.unit_price * Decimal(qty)),
                sizes=[
                    SizeCellRead(
                        id=c.id,
                        size_label=c.size_label,
                        position=c.position,
                        ordered_qty=c.ordered_qty,
                        confirmed_qty=c.confirmed_qty,
                        shipped_qty=c.shipped_qty,
                    )
                    for c in line.cells
                ],
            )
        )
    return SalesOrderRead(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        customer_id=order.customer_id,
        customer_po_number=order.customer_po_number,
        season_id=order.season_id,
        order_date=order.order_date,
        currency=order.currency,
        incoterms=order.incoterms,
        payment_terms=order.payment_terms,
        notes=order.notes,
        total_quantity=sum(l.line_quantity for l in lines),
        total_shipped_quantity=sum(c.shipped_qty for line in order.lines for c in line.cells),
        total_value=order_value(order),
        lines=lines,
    )


def _shipment_read(session: Session, shipment: Shipment) -> ShipmentRead:
    lines = session.exec(
        select(ShipmentLine).where(ShipmentLine.shipment_id == shipment.id)
    ).all()
    return ShipmentRead(
        id=shipment.id,
        shipment_number=shipment.shipment_number,
        sales_order_id=shipment.sales_order_id,
        shipment_date=shipment.shipment_date,
        shipping_reference=shipment.shipping_reference,
        destination=shipment.destination,
        notes=shipment.notes,
        status=shipment.status,
        cost_of_goods=shipment.cost_of_goods,
        total_quantity=sum(line.quantity for line in lines),
        total_cartons=sum(line.carton_count for line in lines),
        lines=[
            ShipmentLineRead(
                id=line.id,
                sales_order_line_id=line.sales_order_line_id,
                sales_order_size_cell_id=line.sales_order_size_cell_id,
                size_label=session.get(SalesOrderSizeCell, line.sales_order_size_cell_id).size_label,
                quantity=line.quantity,
                carton_count=line.carton_count,
            )
            for line in lines
        ],
    )


@router.post("", response_model=SalesOrderRead, status_code=201)
def create_order(
    payload: SalesOrderCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    number = next_document_number(session, "SALES_ORDER", "SO")
    # Build the header explicitly (nested `lines` in the payload collide with the
    # ORM relationship); build_order is the single builder of persisted lines.
    order = SalesOrder(
        order_number=number,
        status=SalesOrderStatus.draft,
        customer_id=payload.customer_id,
        customer_po_number=payload.customer_po_number,
        season_id=payload.season_id,
        order_date=payload.order_date,
        currency=payload.currency,
        incoterms=payload.incoterms,
        payment_terms=payload.payment_terms,
        notes=payload.notes,
    )
    session.add(order)
    session.flush()
    try:
        build_order(session, order, payload)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Invalid reference") from exc
    session.refresh(order)
    return _serialize(order)


@router.get("", response_model=List[SalesOrderRead])
def list_orders(
    q: Optional[str] = None,
    status: Optional[SalesOrderStatus] = None,
    customer_id: Optional[int] = None,
    sort: str = "id",
    direction: str = "desc",
    offset: int = 0,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    offset, limit = page_bounds(offset, limit)
    sort_fields = {
        "id": SalesOrder.id,
        "order_number": SalesOrder.order_number,
        "order_date": SalesOrder.order_date,
        "status": SalesOrder.status,
    }
    if sort not in sort_fields or direction not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail="Invalid sort field or direction")
    stmt = select(SalesOrder)
    if q:
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(SalesOrder.order_number).like(pattern)
            | func.lower(func.coalesce(SalesOrder.customer_po_number, "")).like(pattern)
        )
    if status is not None:
        stmt = stmt.where(SalesOrder.status == status)
    if customer_id is not None:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    order_column = sort_fields[sort]
    stmt = stmt.order_by(order_column.desc() if direction == "desc" else order_column.asc())
    orders = session.exec(stmt.offset(offset).limit(limit)).all()
    return [_serialize(o) for o in orders]


@router.get("/export")
def export_orders(session: Session = Depends(get_session)):
    orders = session.exec(select(SalesOrder).order_by(SalesOrder.id.desc())).all()
    rows = []
    for order in orders:
        data = _serialize(order)
        rows.append({
            "order_number": data.order_number,
            "status": data.status.value,
            "customer_id": data.customer_id,
            "customer_po": data.customer_po_number,
            "order_date": data.order_date,
            "currency": data.currency,
            "quantity": data.total_quantity,
            "value": data.total_value,
        })
    return csv_download(
        "sales-orders.csv",
        [
            ("order_number", "Order"), ("status", "Status"),
            ("customer_id", "Customer ID"), ("customer_po", "Customer PO"),
            ("order_date", "Order Date"), ("currency", "Currency"),
            ("quantity", "Quantity"), ("value", "Value"),
        ],
        rows,
    )


@router.get("/{order_id}", response_model=SalesOrderRead)
def get_order(order_id: int, session: Session = Depends(get_session)):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    return _serialize(order)


@router.get("/{order_id}/shipments", response_model=List[ShipmentRead])
def list_shipments(order_id: int, session: Session = Depends(get_session)):
    if session.get(SalesOrder, order_id) is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    shipments = session.exec(
        select(Shipment).where(Shipment.sales_order_id == order_id).order_by(Shipment.id.desc())
    ).all()
    return [_shipment_read(session, shipment) for shipment in shipments]


@router.get("/{order_id}/material-requirements", response_model=List[MaterialRequirementRead])
def material_requirements_for_order(order_id: int, session: Session = Depends(get_session)):
    if session.get(SalesOrder, order_id) is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    return session.exec(
        select(SalesOrderMaterialRequirement)
        .where(SalesOrderMaterialRequirement.sales_order_id == order_id)
        .order_by(SalesOrderMaterialRequirement.material_id)
    ).all()


@router.post("/{order_id}/material-requirements/recalculate", response_model=List[MaterialRequirementRead])
def recalculate_order_material_requirements(
    order_id: int,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser, Role.planner)),
):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    try:
        rows = recalculate_material_requirements(session, order)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return rows


@router.get("/{order_id}/commercial-checks", response_model=CommercialCheckRead)
def get_commercial_checks(order_id: int, session: Session = Depends(get_session)):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    try:
        return commercial_checks(session, order)
    except SalesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{order_id}/history", response_model=List[SalesOrderRevisionRead])
def order_history(order_id: int, session: Session = Depends(get_session)):
    if session.get(SalesOrder, order_id) is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    return session.exec(
        select(SalesOrderRevision)
        .where(SalesOrderRevision.sales_order_id == order_id)
        .order_by(SalesOrderRevision.revision_no.desc())
    ).all()


@router.post("/{order_id}/amend", response_model=SalesOrderRead)
def amend(
    order_id: int,
    payload: SalesOrderAmendRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.merchandiser)),
):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    try:
        amend_order(session, order, payload, actor)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(order)
    return _serialize(order)


@router.post("/{order_id}/cancel", response_model=SalesOrderRead)
def cancel(
    order_id: int,
    payload: SalesOrderCancelRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.merchandiser)),
):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    try:
        sales_order_state_machine.assert_transition(order.status.value, SalesOrderStatus.cancelled.value)
        record_revision(session, order, action="cancelled", reason=payload.reason, actor=actor)
    except InvalidTransition as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    order.status = SalesOrderStatus.cancelled
    session.add(order)
    session.commit()
    session.refresh(order)
    return _serialize(order)


@router.post("/{order_id}/confirm", response_model=SalesOrderRead)
def confirm(
    order_id: int,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    try:
        confirm_order(session, order)
    except InvalidTransition as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(order)
    return _serialize(order)


@router.post("/{order_id}/ship", response_model=SalesOrderRead)
def ship(
    order_id: int,
    payload: Optional[ShipmentCreate] = None,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.merchandiser)),
):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    # AQL final inspection gates shipment (7.4).
    if not order_has_passed_final(session, order_id):
        raise HTTPException(
            status_code=409,
            detail="Order cannot ship without a passed AQL final inspection",
    )
    try:
        ship_order(session, order, payload or ShipmentCreate(), actor)
    except InvalidTransition as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    session.refresh(order)
    return _serialize(order)


@router.post("/{order_id}/close", response_model=SalesOrderRead)
def close(
    order_id: int,
    payload: SalesOrderCloseRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.merchandiser)),
):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    try:
        close_order(session, order, payload.reason, actor)
    except InvalidTransition as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(order)
    return _serialize(order)
