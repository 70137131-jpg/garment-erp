"""Sales-order lifecycle: creation, the size-matrix build, and confirmation."""

from decimal import Decimal
from typing import Dict

from sqlmodel import Session, select

from ..events import SalesOrderConfirmed, ShipmentDispatched
from ..kernel.events import emit
from ..kernel.state_machine import StateMachine
from ..kernel.types import quantize_money
from ..masters.models import SizeRangeItem
from ..styles.models import Style
from .models import (
    SalesOrder,
    SalesOrderCreate,
    SalesOrderLine,
    SalesOrderSizeCell,
    SalesOrderStatus,
)

sales_order_state_machine = StateMachine(
    transitions={
        SalesOrderStatus.draft.value: {
            SalesOrderStatus.confirmed.value,
            SalesOrderStatus.cancelled.value,
        },
        SalesOrderStatus.confirmed.value: {
            SalesOrderStatus.in_production.value,
            SalesOrderStatus.shipped.value,
            SalesOrderStatus.cancelled.value,
        },
        SalesOrderStatus.in_production.value: {
            SalesOrderStatus.shipped.value,
            SalesOrderStatus.cancelled.value,
        },
        SalesOrderStatus.shipped.value: {SalesOrderStatus.closed.value},
        SalesOrderStatus.closed.value: set(),
        SalesOrderStatus.cancelled.value: set(),
    },
    initial=SalesOrderStatus.draft.value,
)


class SalesError(Exception):
    """Domain error building or transitioning a sales order."""


def build_order(
    session: Session, order: SalesOrder, payload: SalesOrderCreate
) -> SalesOrder:
    """Attach lines and the normalized size matrix, validating each size label
    against the line's style size range."""
    for line_in in payload.lines:
        style = session.get(Style, line_in.style_id)
        if style is None:
            raise SalesError(f"Style {line_in.style_id} not found")
        size_items: Dict[str, SizeRangeItem] = {
            item.label: item
            for item in session.exec(
                select(SizeRangeItem).where(
                    SizeRangeItem.size_range_id == style.size_range_id
                )
            ).all()
        }

        line = SalesOrderLine(
            sales_order_id=order.id,
            style_id=line_in.style_id,
            colour_id=line_in.colour_id,
            unit_price=line_in.unit_price,
            delivery_date=line_in.delivery_date,
            destination=line_in.destination,
            packing_ratio=line_in.packing_ratio,
        )
        session.add(line)
        session.flush()

        for cell in line_in.sizes:
            item = size_items.get(cell.size_label)
            if item is None:
                raise SalesError(
                    f"Size '{cell.size_label}' is not in style {style.style_number}'s range"
                )
            session.add(
                SalesOrderSizeCell(
                    line_id=line.id,
                    size_range_item_id=item.id,
                    position=item.position,
                    size_label=item.label,
                    ordered_qty=cell.ordered_qty,
                )
            )
    session.flush()
    session.refresh(order)
    return order


def order_value(order: SalesOrder, *, use_confirmed: bool = False) -> Decimal:
    total = Decimal("0")
    for line in order.lines:
        qty = sum(
            (c.confirmed_qty if use_confirmed else c.ordered_qty) for c in line.cells
        )
        total += line.unit_price * Decimal(qty)
    return quantize_money(total)


def confirm_order(session: Session, order: SalesOrder) -> SalesOrder:
    """Confirm: freeze confirmed = ordered per cell, advance status, emit event."""
    sales_order_state_machine.assert_transition(
        order.status.value, SalesOrderStatus.confirmed.value
    )
    if not order.lines:
        raise SalesError("Cannot confirm an order with no lines")

    for line in order.lines:
        for cell in line.cells:
            cell.confirmed_qty = cell.ordered_qty
            session.add(cell)

    order.status = SalesOrderStatus.confirmed
    session.add(order)
    session.flush()

    total = order_value(order, use_confirmed=True)
    emit(
        SalesOrderConfirmed(
            sales_order_id=order.id,
            order_number=order.order_number,
            customer_id=order.customer_id,
            currency=order.currency,
            total_value=total,
        )
    )
    return order


def ship_order(session: Session, order: SalesOrder) -> SalesOrder:
    """Ship a confirmed order: fill shipped = confirmed per cell, advance status,
    and emit ``ShipmentDispatched`` (the AR trigger). The AQL gate is enforced by
    the caller — an order only reaches here with a passed final inspection.
    """
    sales_order_state_machine.assert_transition(
        order.status.value, SalesOrderStatus.shipped.value
    )
    for line in order.lines:
        for cell in line.cells:
            cell.shipped_qty = cell.confirmed_qty
            session.add(cell)
    order.status = SalesOrderStatus.shipped
    session.add(order)
    session.flush()

    invoice_value = order_value(order, use_confirmed=True)
    emit(
        ShipmentDispatched(
            sales_order_id=order.id,
            order_number=order.order_number,
            customer_id=order.customer_id,
            currency=order.currency,
            invoice_value=invoice_value,
        )
    )
    return order
