"""Sales-order lifecycle, commercial controls, and auditable amendments."""

import json
from decimal import Decimal
from typing import Dict

from sqlalchemy import func
from sqlmodel import Session, select

from ..events import SalesOrderConfirmed, ShipmentDispatched
from ..finance.models import ARInvoice
from ..inventory.models import Roll, RollStatus, StockLedgerEntry
from ..inventory.service import roll_available_qty
from ..kernel.events import emit
from ..kernel.numbering import next_document_number
from ..kernel.state_machine import StateMachine
from ..kernel.types import quantize_money, quantize_qty
from ..masters.models import Customer, SizeRangeItem
from ..styles.models import BomStatus, BomVersion, Style
from ..styles.service import material_requirements
from .models import (
    SalesOrder,
    SalesOrderCreate,
    SalesOrderLine,
    SalesOrderMaterialRequirement,
    SalesOrderRevision,
    SalesOrderSizeCell,
    SalesOrderStatus,
    Shipment,
    ShipmentCreate,
    ShipmentLine,
)

sales_order_state_machine = StateMachine(
    transitions={
        SalesOrderStatus.draft.value: {
            SalesOrderStatus.confirmed.value,
            SalesOrderStatus.cancelled.value,
        },
        SalesOrderStatus.confirmed.value: {
            SalesOrderStatus.in_production.value,
            SalesOrderStatus.partially_shipped.value,
            SalesOrderStatus.shipped.value,
            SalesOrderStatus.cancelled.value,
        },
        SalesOrderStatus.in_production.value: {
            SalesOrderStatus.partially_shipped.value,
            SalesOrderStatus.shipped.value,
            SalesOrderStatus.cancelled.value,
        },
        SalesOrderStatus.partially_shipped.value: {SalesOrderStatus.shipped.value},
        SalesOrderStatus.shipped.value: {SalesOrderStatus.closed.value},
        SalesOrderStatus.closed.value: set(),
        SalesOrderStatus.cancelled.value: set(),
    },
    initial=SalesOrderStatus.draft.value,
)


class SalesError(Exception):
    """Domain error building or transitioning a sales order."""


def _usable_stock(session: Session, material_id: int) -> Decimal:
    """Stock that is usable for a new order, excluding unapproved fabric."""
    rolls = session.exec(
        select(Roll).where(
            Roll.material_id == material_id,
            Roll.status.in_([RollStatus.available, RollStatus.reserved]),
        )
    ).all()
    roll_qty = sum((roll_available_qty(session, roll) for roll in rolls), Decimal("0"))
    unassigned_qty = session.exec(
        select(func.coalesce(func.sum(StockLedgerEntry.quantity), 0)).where(
            StockLedgerEntry.material_id == material_id,
            StockLedgerEntry.roll_id.is_(None),
        )
    ).one()
    return quantize_qty(roll_qty + Decimal(unassigned_qty))


def recalculate_material_requirements(
    session: Session, order: SalesOrder
) -> list[SalesOrderMaterialRequirement]:
    """Explode approved BOMs and persist the current shortage by material."""
    if not order.lines:
        raise SalesError("Cannot calculate requirements for an order with no lines")

    required_by_material: dict[int, Decimal] = {}
    for line in order.lines:
        bom = session.exec(
            select(BomVersion)
            .where(
                BomVersion.style_id == line.style_id,
                BomVersion.status == BomStatus.approved,
            )
            .order_by(BomVersion.version_no.desc())
        ).first()
        # Legacy/customer-supplied styles may be confirmed before their BOM is
        # released.  They remain operationally visible, while only released
        # BOMs contribute purchasable demand.
        if bom is None:
            continue
        size_qty = {
            cell.size_label: cell.confirmed_qty or cell.ordered_qty
            for cell in line.cells
        }
        for material_id, quantity in material_requirements(session, bom, size_qty).items():
            required_by_material[material_id] = quantize_qty(
                required_by_material.get(material_id, Decimal("0")) + quantity
            )

    for row in session.exec(
        select(SalesOrderMaterialRequirement).where(
            SalesOrderMaterialRequirement.sales_order_id == order.id
        )
    ).all():
        session.delete(row)
    session.flush()

    rows: list[SalesOrderMaterialRequirement] = []
    for material_id, required_qty in sorted(required_by_material.items()):
        available_qty = _usable_stock(session, material_id)
        row = SalesOrderMaterialRequirement(
            sales_order_id=order.id,
            material_id=material_id,
            required_qty=required_qty,
            available_qty=available_qty,
            shortage_qty=quantize_qty(max(Decimal("0"), required_qty - available_qty)),
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def _snapshot(order: SalesOrder) -> str:
    return json.dumps(
        {
            "order_number": order.order_number,
            "status": order.status.value,
            "customer_id": order.customer_id,
            "customer_po_number": order.customer_po_number,
            "currency": order.currency,
            "order_date": order.order_date,
            "incoterms": order.incoterms,
            "payment_terms": order.payment_terms,
            "notes": order.notes,
            "lines": [
                {
                    "style_id": line.style_id,
                    "colour_id": line.colour_id,
                    "unit_price": line.unit_price,
                    "delivery_date": line.delivery_date,
                    "destination": line.destination,
                    "packing_ratio": line.packing_ratio,
                    "sizes": [
                        {"size_label": cell.size_label, "ordered_qty": cell.ordered_qty}
                        for cell in line.cells
                    ],
                }
                for line in order.lines
            ],
        },
        default=str,
        separators=(",", ":"),
    )


def record_revision(
    session: Session,
    order: SalesOrder,
    *,
    action: str,
    reason: str,
    actor: str,
) -> SalesOrderRevision:
    reason = reason.strip()
    if not reason:
        raise SalesError("A reason is required")
    revision_no = session.exec(
        select(func.coalesce(func.max(SalesOrderRevision.revision_no), 0)).where(
            SalesOrderRevision.sales_order_id == order.id
        )
    ).one() + 1
    revision = SalesOrderRevision(
        sales_order_id=order.id,
        revision_no=revision_no,
        action=action,
        reason=reason,
        snapshot_json=_snapshot(order),
        created_by=actor,
    )
    session.add(revision)
    session.flush()
    return revision


def commercial_checks(session: Session, order: SalesOrder) -> dict:
    customer = session.get(Customer, order.customer_id)
    if customer is None:
        raise SalesError("Customer not found")
    messages: list[str] = []
    value = order_value(order)
    if order.currency != customer.currency:
        messages.append(
            f"Order currency {order.currency} differs from customer currency {customer.currency}"
        )
    for line in order.lines:
        if line.unit_price <= 0:
            messages.append(f"Style {line.style_id} has a non-positive selling price")

    receivables = session.exec(
        select(ARInvoice).where(ARInvoice.customer_id == customer.id)
    ).all()
    exposure = sum(
        (invoice.amount - invoice.settled_amount for invoice in receivables), Decimal("0")
    )
    open_orders = session.exec(
        select(SalesOrder).where(
            SalesOrder.customer_id == customer.id,
            SalesOrder.id != order.id,
            SalesOrder.status.in_([
                SalesOrderStatus.confirmed,
                SalesOrderStatus.in_production,
            ]),
        )
    ).all()
    exposure += sum((order_value(open_order, use_confirmed=True) for open_order in open_orders), Decimal("0"))
    projected = quantize_money(exposure + value)
    if customer.credit_limit > 0 and projected > customer.credit_limit:
        messages.append(
            f"Credit limit exceeded: projected {projected} exceeds {customer.credit_limit}"
        )
    return {
        "eligible": not messages,
        "order_value": value,
        "credit_limit": customer.credit_limit,
        "current_exposure": quantize_money(exposure),
        "projected_exposure": projected,
        "messages": messages,
    }


def amend_order(session: Session, order: SalesOrder, payload, actor: str) -> SalesOrder:
    if order.status not in (SalesOrderStatus.draft, SalesOrderStatus.confirmed):
        raise SalesError(f"Cannot amend a '{order.status.value}' order")
    was_confirmed = order.status == SalesOrderStatus.confirmed
    record_revision(session, order, action="amended", reason=payload.reason, actor=actor)
    for field in ("customer_po_number", "order_date", "incoterms", "payment_terms", "notes"):
        value = getattr(payload, field)
        if value is not None:
            setattr(order, field, value)
    session.add(order)
    if payload.lines is not None:
        for line in list(order.lines):
            session.delete(line)
        session.flush()
        replacement = SalesOrderCreate(
            customer_id=order.customer_id,
            customer_po_number=order.customer_po_number,
            season_id=order.season_id,
            order_date=order.order_date,
            currency=order.currency,
            incoterms=order.incoterms,
            payment_terms=order.payment_terms,
            notes=order.notes,
            lines=payload.lines,
        )
        build_order(session, order, replacement)
        if was_confirmed:
            for line in order.lines:
                for cell in line.cells:
                    cell.confirmed_qty = cell.ordered_qty
                    session.add(cell)
    session.flush()
    session.refresh(order)
    return order


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
    checks = commercial_checks(session, order)
    if not checks["eligible"]:
        raise SalesError("; ".join(checks["messages"]))

    for line in order.lines:
        for cell in line.cells:
            cell.confirmed_qty = cell.ordered_qty
            session.add(cell)

    order.status = SalesOrderStatus.confirmed
    session.add(order)
    session.flush()

    # A confirmed order is the material-planning trigger.  Persist the BOM
    # explosion now so procurement sees a stable, auditable shortage snapshot.
    recalculate_material_requirements(session, order)

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


def _ship_order_legacy(session: Session, order: SalesOrder) -> SalesOrder:
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


def ship_order(
    session: Session, order: SalesOrder, payload: ShipmentCreate, actor: str
) -> Shipment:
    """Dispatch selected size quantities and advance the order cumulatively."""
    if order.status not in (
        SalesOrderStatus.confirmed,
        SalesOrderStatus.in_production,
        SalesOrderStatus.partially_shipped,
    ):
        sales_order_state_machine.assert_transition(order.status.value, SalesOrderStatus.shipped.value)

    requested = list(payload.lines)
    if not requested:
        requested = [
            (cell.id, cell.confirmed_qty - cell.shipped_qty, 0)
            for line in order.lines
            for cell in line.cells
            if cell.confirmed_qty > cell.shipped_qty
        ]
    if not requested:
        raise SalesError("There is no outstanding quantity to ship")

    line_by_cell = {cell.id: line for line in order.lines for cell in line.cells}
    seen_cells: set[int] = set()
    prepared: list[tuple[SalesOrderSizeCell, SalesOrderLine, int, int]] = []
    for item in requested:
        cell_id, quantity, cartons = item if isinstance(item, tuple) else (
            item.sales_order_size_cell_id, item.quantity, item.carton_count
        )
        if cell_id in seen_cells:
            raise SalesError("A size cell may appear only once on a shipment")
        seen_cells.add(cell_id)
        cell = session.get(SalesOrderSizeCell, cell_id)
        line = line_by_cell.get(cell_id)
        if cell is None or line is None:
            raise SalesError("Shipment line does not belong to this sales order")
        if quantity <= 0 or cartons < 0:
            raise SalesError("Shipment quantities must be positive and carton counts non-negative")
        outstanding = cell.confirmed_qty - cell.shipped_qty
        if quantity > outstanding:
            raise SalesError(f"Size {cell.size_label} has only {outstanding} units outstanding")
        prepared.append((cell, line, quantity, cartons))

    shipment = Shipment(
        shipment_number=next_document_number(session, "SHIPMENT", "SHP"),
        sales_order_id=order.id,
        shipment_date=payload.shipment_date,
        shipping_reference=payload.shipping_reference,
        destination=payload.destination,
        notes=payload.notes,
        created_by=actor,
    )
    session.add(shipment)
    session.flush()
    invoice_value = Decimal("0")
    for cell, line, quantity, cartons in prepared:
        session.add(ShipmentLine(
            shipment_id=shipment.id,
            sales_order_line_id=line.id,
            sales_order_size_cell_id=cell.id,
            quantity=quantity,
            carton_count=cartons,
        ))
        cell.shipped_qty += quantity
        session.add(cell)
        invoice_value += line.unit_price * Decimal(quantity)

    all_shipped = all(cell.shipped_qty >= cell.confirmed_qty for line in order.lines for cell in line.cells)
    target = SalesOrderStatus.shipped if all_shipped else SalesOrderStatus.partially_shipped
    if order.status != SalesOrderStatus.partially_shipped or target == SalesOrderStatus.shipped:
        sales_order_state_machine.assert_transition(order.status.value, target.value)
    order.status = target
    record_revision(
        session, order, action="shipment_dispatched",
        reason=payload.shipping_reference or f"Shipment {shipment.shipment_number} dispatched",
        actor=actor,
    )
    session.add(order)
    session.flush()
    emit(ShipmentDispatched(
        sales_order_id=order.id,
        order_number=order.order_number,
        customer_id=order.customer_id,
        currency=order.currency,
        invoice_value=quantize_money(invoice_value),
        shipment_id=shipment.id,
    ))
    return shipment


def close_order(session: Session, order: SalesOrder, reason: str, actor: str) -> SalesOrder:
    sales_order_state_machine.assert_transition(order.status.value, SalesOrderStatus.closed.value)
    record_revision(session, order, action="closed", reason=reason, actor=actor)
    order.status = SalesOrderStatus.closed
    session.add(order)
    session.flush()
    return order
