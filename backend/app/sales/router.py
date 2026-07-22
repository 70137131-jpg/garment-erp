from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.numbering import next_document_number
from ..kernel.rbac import Role, require_roles
from ..kernel.state_machine import InvalidTransition
from .models import (
    SalesOrder,
    SalesOrderCreate,
    SalesOrderLineRead,
    SalesOrderRead,
    SalesOrderStatus,
    SizeCellRead,
)
from ..quality.service import order_has_passed_final
from .service import SalesError, build_order, confirm_order, order_value, ship_order

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
        total_value=order_value(order),
        lines=lines,
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
def list_orders(session: Session = Depends(get_session)):
    orders = session.exec(select(SalesOrder).order_by(SalesOrder.id)).all()
    return [_serialize(o) for o in orders]


@router.get("/{order_id}", response_model=SalesOrderRead)
def get_order(order_id: int, session: Session = Depends(get_session)):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
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
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
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
        ship_order(session, order)
    except InvalidTransition as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    session.refresh(order)
    return _serialize(order)
