"""Purchase-order helpers + the goods-receipt atomic transaction.

``post_goods_receipt`` is the pivot: PO update, ledger movements, roll creation
and event emission all happen inside one caller-owned transaction so they commit
together or not at all.
"""

from decimal import Decimal
from typing import Dict

from sqlmodel import Session, select

from ..events import GoodsReceiptPosted, SupplierInvoiceRaised
from ..inventory.models import Grade, MovementType, Roll, RollStatus
from ..inventory.service import post_movement
from ..kernel.events import emit
from ..kernel.numbering import next_document_number
from ..kernel.types import quantize_money, quantize_qty
from ..masters.models import Material
from .models import (
    GoodsReceipt,
    GoodsReceiptCreate,
    GoodsReceiptRoll,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)


class ProcurementError(Exception):
    """Domain error building a PO or posting a receipt."""


def build_purchase_order(
    session: Session, order: PurchaseOrder, payload
) -> PurchaseOrder:
    for line_in in payload.lines:
        material = session.get(Material, line_in.material_id)
        if material is None:
            raise ProcurementError(f"Material {line_in.material_id} not found")
        if line_in.ordered_qty <= 0:
            raise ProcurementError("Ordered quantity must be positive")
        session.add(
            PurchaseOrderLine(
                purchase_order_id=order.id,
                material_id=line_in.material_id,
                colour_id=line_in.colour_id,
                ordered_qty=line_in.ordered_qty,
                unit_price=line_in.unit_price,
                uom=line_in.uom,
                construction=line_in.construction,
                composition=line_in.composition,
                width_cm=line_in.width_cm,
                gsm=line_in.gsm,
                finish=line_in.finish,
                inspection_standard=line_in.inspection_standard,
            )
        )
    session.flush()
    session.refresh(order)
    return order


def po_total_value(order: PurchaseOrder) -> Decimal:
    return quantize_money(
        sum((l.unit_price * l.ordered_qty for l in order.lines), Decimal("0"))
    )


def _refresh_po_status(order: PurchaseOrder) -> None:
    """Advance PO status from received-vs-ordered across all lines."""
    if order.status in (PurchaseOrderStatus.cancelled, PurchaseOrderStatus.closed):
        return
    total_ordered = sum((l.ordered_qty for l in order.lines), Decimal("0"))
    total_received = sum((l.received_qty for l in order.lines), Decimal("0"))
    if total_received <= 0:
        return
    if total_received >= total_ordered:
        order.status = PurchaseOrderStatus.received
    else:
        order.status = PurchaseOrderStatus.partially_received


def post_goods_receipt(
    session: Session, payload: GoodsReceiptCreate, actor: str
) -> GoodsReceipt:
    """Post a goods receipt: create rolls, move stock, update PO, emit events.

    Every roll arrives ``pending_inspection`` (7.1 gates it to available); stock
    is on the ledger immediately (physically present) but not yet usable until
    inspection approves it.
    """
    order = session.get(PurchaseOrder, payload.purchase_order_id)
    if order is None:
        raise ProcurementError("Purchase order not found")
    if order.status in (PurchaseOrderStatus.cancelled, PurchaseOrderStatus.closed):
        raise ProcurementError(f"Cannot receive against a '{order.status.value}' PO")
    if not payload.rolls:
        raise ProcurementError("Goods receipt must contain at least one roll")

    lines_by_id: Dict[int, PurchaseOrderLine] = {l.id: l for l in order.lines}

    number = next_document_number(session, "GOODS_RECEIPT", "GR")
    receipt = GoodsReceipt(
        receipt_number=number,
        purchase_order_id=order.id,
        supplier_id=order.supplier_id,
        received_date=payload.received_date,
        note=payload.note,
    )
    session.add(receipt)
    session.flush()

    total_length = Decimal("0")
    total_value = Decimal("0")
    for roll_in in payload.rolls:
        line = lines_by_id.get(roll_in.purchase_order_line_id)
        if line is None:
            raise ProcurementError(
                f"PO line {roll_in.purchase_order_line_id} is not on this PO"
            )
        if roll_in.length <= 0:
            raise ProcurementError("Roll length must be positive")

        roll_number = roll_in.roll_number or next_document_number(
            session, "ROLL", "ROLL", width=6
        )
        roll = Roll(
            roll_number=roll_number,
            material_id=line.material_id,
            dye_lot=roll_in.dye_lot,
            shade_code=roll_in.shade_code,
            shade_group=roll_in.shade_group,
            length=quantize_qty(roll_in.length),
            weight=roll_in.weight,
            width_cm=roll_in.width_cm,
            gsm=roll_in.gsm,
            grade=Grade(roll_in.grade),
            warehouse=roll_in.warehouse,
            location=roll_in.location,
            status=RollStatus.pending_inspection,
            supplier_id=order.supplier_id,
            goods_receipt_id=receipt.id,
        )
        session.add(roll)
        session.flush()

        post_movement(
            session,
            material_id=line.material_id,
            movement_type=MovementType.receipt,
            quantity=roll_in.length,
            uom=line.uom,
            roll_id=roll.id,
            warehouse=roll_in.warehouse,
            location=roll_in.location,
            lot=roll_in.dye_lot,
            reference_type="goods_receipt",
            reference_id=receipt.id,
            actor=actor,
        )

        session.add(
            GoodsReceiptRoll(
                goods_receipt_id=receipt.id,
                purchase_order_line_id=line.id,
                roll_id=roll.id,
                length=quantize_qty(roll_in.length),
            )
        )

        line.received_qty = quantize_qty(line.received_qty + roll_in.length)
        session.add(line)
        total_length += roll_in.length
        total_value += line.unit_price * roll_in.length

    _refresh_po_status(order)
    session.add(order)
    session.flush()
    session.refresh(receipt)

    total_value = quantize_money(total_value)
    emit(
        GoodsReceiptPosted(
            goods_receipt_id=receipt.id,
            receipt_number=receipt.receipt_number,
            supplier_id=order.supplier_id,
            purchase_order_id=order.id,
            currency=order.currency,
            total_value=total_value,
        )
    )
    emit(
        SupplierInvoiceRaised(
            goods_receipt_id=receipt.id,
            supplier_id=order.supplier_id,
            currency=order.currency,
            amount=total_value,
            reference=receipt.receipt_number,
        )
    )
    return receipt
