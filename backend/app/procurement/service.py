"""Purchase-order helpers + the goods-receipt atomic transaction.

``post_goods_receipt`` is the pivot: PO update, ledger movements, roll creation
and event emission all happen inside one caller-owned transaction so they commit
together or not at all.
"""

import json
from decimal import Decimal
from typing import Dict

from sqlalchemy import func
from sqlmodel import Session, select

from ..events import GoodsReceiptPosted, SupplierInvoiceRaised
from ..inventory.models import Grade, MovementType, Roll, RollStatus
from ..inventory.service import post_movement
from ..kernel.events import emit
from ..kernel.numbering import next_document_number
from ..kernel.types import quantize_money, quantize_qty
from ..masters.models import Material
from ..sales.models import SalesOrder, SalesOrderMaterialRequirement
from .models import (
    GoodsReceipt,
    GoodsReceiptCreate,
    GoodsReceiptRoll,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderFromRequisitions,
    PurchaseOrderRequisitionLine,
    PurchaseRequisition,
    PurchaseRequisitionCreate,
    PurchaseRequisitionLine,
    PurchaseRequisitionStatus,
    PurchaseOrderRevision,
    PurchaseOrderStatus,
)


class ProcurementError(Exception):
    """Domain error building a PO or posting a receipt."""


def _refresh_requisition_status(session: Session, requisition_id: int) -> None:
    requisition = session.get(PurchaseRequisition, requisition_id)
    if requisition is None or requisition.status == PurchaseRequisitionStatus.cancelled:
        return
    lines = session.exec(
        select(PurchaseRequisitionLine).where(
            PurchaseRequisitionLine.purchase_requisition_id == requisition_id
        )
    ).all()
    if lines and all(line.ordered_qty >= line.requested_qty for line in lines):
        requisition.status = PurchaseRequisitionStatus.ordered
    elif any(line.ordered_qty > 0 for line in lines):
        requisition.status = PurchaseRequisitionStatus.partially_ordered
    else:
        requisition.status = PurchaseRequisitionStatus.draft
    session.add(requisition)


def create_purchase_requisition(
    session: Session, payload: PurchaseRequisitionCreate, actor: str
) -> PurchaseRequisition:
    if payload.sales_order_id is not None and session.get(SalesOrder, payload.sales_order_id) is None:
        raise ProcurementError("Sales order not found")
    if not payload.lines:
        raise ProcurementError("A purchase requisition needs at least one line")
    requisition = PurchaseRequisition(
        requisition_number=next_document_number(session, "PURCHASE_REQUISITION", "PR"),
        sales_order_id=payload.sales_order_id,
        suggested_supplier_id=payload.suggested_supplier_id,
        requested_date=payload.requested_date,
        notes=payload.notes,
        created_by=actor,
    )
    session.add(requisition)
    session.flush()
    for line_in in payload.lines:
        if line_in.requested_qty <= 0:
            raise ProcurementError("Requested quantity must be positive")
        if session.get(Material, line_in.material_id) is None:
            raise ProcurementError(f"Material {line_in.material_id} not found")
        session.add(PurchaseRequisitionLine(
            purchase_requisition_id=requisition.id,
            material_id=line_in.material_id,
            requested_qty=quantize_qty(line_in.requested_qty),
            uom=line_in.uom,
            note=line_in.note,
        ))
    session.flush()
    return requisition


def create_requisition_from_shortages(
    session: Session, sales_order_id: int, actor: str
) -> PurchaseRequisition:
    if session.get(SalesOrder, sales_order_id) is None:
        raise ProcurementError("Sales order not found")
    requirements = session.exec(
        select(SalesOrderMaterialRequirement).where(
            SalesOrderMaterialRequirement.sales_order_id == sales_order_id,
            SalesOrderMaterialRequirement.shortage_qty > 0,
        )
    ).all()
    if not requirements:
        raise ProcurementError("This order has no material shortages")

    requisition = PurchaseRequisition(
        requisition_number=next_document_number(session, "PURCHASE_REQUISITION", "PR"),
        sales_order_id=sales_order_id,
        created_by=actor,
        notes="Generated from confirmed sales-order shortages",
    )
    session.add(requisition)
    session.flush()
    lines_added = 0
    for requirement in requirements:
        existing = session.exec(
            select(PurchaseRequisitionLine).where(
                PurchaseRequisitionLine.material_requirement_id == requirement.id
            )
        ).first()
        if existing is not None:
            existing_requisition = session.get(PurchaseRequisition, existing.purchase_requisition_id)
            if existing_requisition and existing_requisition.status != PurchaseRequisitionStatus.cancelled:
                continue
        material = session.get(Material, requirement.material_id)
        session.add(PurchaseRequisitionLine(
            purchase_requisition_id=requisition.id,
            material_requirement_id=requirement.id,
            material_id=requirement.material_id,
            requested_qty=requirement.shortage_qty,
            uom=material.base_uom if material else "metre",
        ))
        lines_added += 1
    if not lines_added:
        session.delete(requisition)
        session.flush()
        raise ProcurementError("All current shortages already have active requisitions")
    session.flush()
    return requisition


def create_purchase_order_from_requisitions(
    session: Session, payload: PurchaseOrderFromRequisitions
) -> PurchaseOrder:
    if not payload.lines:
        raise ProcurementError("Select at least one requisition line")
    order = PurchaseOrder(
        order_number=next_document_number(session, "PURCHASE_ORDER", "PO"),
        status=(PurchaseOrderStatus.pending_approval if payload.requires_approval else PurchaseOrderStatus.issued),
        requires_approval=payload.requires_approval,
        supplier_id=payload.supplier_id,
        currency=payload.currency,
        order_date=payload.order_date,
        expected_date=payload.expected_date,
        notes=payload.notes,
    )
    session.add(order)
    session.flush()
    affected_requisitions: set[int] = set()
    used_requisition_lines: set[int] = set()
    for line_in in payload.lines:
        if line_in.ordered_qty <= 0 or not line_in.requisition_line_ids:
            raise ProcurementError("Each PO line needs quantity and requisition lines")
        requisition_lines = []
        for requisition_line_id in line_in.requisition_line_ids:
            if requisition_line_id in used_requisition_lines:
                raise ProcurementError("A requisition line may appear on only one PO line")
            requisition_line = session.get(PurchaseRequisitionLine, requisition_line_id)
            if requisition_line is None:
                raise ProcurementError(f"Requisition line {requisition_line_id} not found")
            if requisition_line.material_id != line_in.material_id:
                raise ProcurementError("Requisition lines must match the PO material")
            outstanding = quantize_qty(requisition_line.requested_qty - requisition_line.ordered_qty)
            if outstanding <= 0:
                raise ProcurementError(f"Requisition line {requisition_line_id} is already ordered")
            requisition_lines.append((requisition_line, outstanding))
            used_requisition_lines.add(requisition_line_id)
        total_outstanding = sum((item[1] for item in requisition_lines), Decimal("0"))
        if line_in.ordered_qty > total_outstanding:
            raise ProcurementError("PO quantity exceeds the selected requisition balance")
        po_line = PurchaseOrderLine(
            purchase_order_id=order.id,
            material_id=line_in.material_id,
            colour_id=line_in.colour_id,
            ordered_qty=quantize_qty(line_in.ordered_qty),
            unit_price=line_in.unit_price,
            uom=line_in.uom,
            construction=line_in.construction,
            composition=line_in.composition,
            width_cm=line_in.width_cm,
            gsm=line_in.gsm,
            finish=line_in.finish,
            inspection_standard=line_in.inspection_standard,
        )
        session.add(po_line)
        session.flush()
        remaining = po_line.ordered_qty
        for requisition_line, outstanding in requisition_lines:
            allocated = min(remaining, outstanding)
            session.add(PurchaseOrderRequisitionLine(
                purchase_order_line_id=po_line.id,
                purchase_requisition_line_id=requisition_line.id,
                allocated_qty=allocated,
            ))
            requisition_line.ordered_qty = quantize_qty(requisition_line.ordered_qty + allocated)
            session.add(requisition_line)
            affected_requisitions.add(requisition_line.purchase_requisition_id)
            remaining -= allocated
            if remaining <= 0:
                break
    for requisition_id in affected_requisitions:
        _refresh_requisition_status(session, requisition_id)
    session.flush()
    session.refresh(order)
    return order


def _po_snapshot(order: PurchaseOrder) -> str:
    return json.dumps(
        {
            "order_number": order.order_number,
            "status": order.status.value,
            "supplier_id": order.supplier_id,
            "currency": order.currency,
            "order_date": order.order_date,
            "expected_date": order.expected_date,
            "notes": order.notes,
            "lines": [
                {
                    "material_id": line.material_id,
                    "colour_id": line.colour_id,
                    "ordered_qty": line.ordered_qty,
                    "received_qty": line.received_qty,
                    "unit_price": line.unit_price,
                    "uom": line.uom,
                }
                for line in order.lines
            ],
        },
        default=str,
        separators=(",", ":"),
    )


def record_po_revision(
    session: Session,
    order: PurchaseOrder,
    *,
    action: str,
    reason: str,
    actor: str,
) -> PurchaseOrderRevision:
    reason = reason.strip()
    if not reason:
        raise ProcurementError("A reason or approval comment is required")
    revision_no = session.exec(
        select(func.coalesce(func.max(PurchaseOrderRevision.revision_no), 0)).where(
            PurchaseOrderRevision.purchase_order_id == order.id
        )
    ).one() + 1
    revision = PurchaseOrderRevision(
        purchase_order_id=order.id,
        revision_no=revision_no,
        action=action,
        reason=reason,
        snapshot_json=_po_snapshot(order),
        created_by=actor,
    )
    session.add(revision)
    session.flush()
    return revision


def amend_purchase_order(session: Session, order: PurchaseOrder, payload, actor: str) -> PurchaseOrder:
    if order.status not in (PurchaseOrderStatus.issued, PurchaseOrderStatus.pending_approval):
        raise ProcurementError(f"Cannot amend a '{order.status.value}' purchase order")
    if any(line.received_qty > 0 for line in order.lines):
        raise ProcurementError("A purchase order with receipts cannot be amended")
    record_po_revision(session, order, action="amended", reason=payload.reason, actor=actor)
    if payload.expected_date is not None:
        order.expected_date = payload.expected_date
    if payload.notes is not None:
        order.notes = payload.notes
    if payload.lines is not None:
        for line in list(order.lines):
            session.delete(line)
        session.flush()
        build_purchase_order(session, order, payload)
    if order.requires_approval:
        order.status = PurchaseOrderStatus.pending_approval
        order.approved_by = None
        order.approved_at = None
    session.add(order)
    session.flush()
    session.refresh(order)
    return order


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
    if order.status not in (
        PurchaseOrderStatus.issued,
        PurchaseOrderStatus.partially_received,
        PurchaseOrderStatus.received,
    ):
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
            unit_cost=line.unit_price,
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
