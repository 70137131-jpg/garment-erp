from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..db import get_session
from ..inventory.models import Roll
from ..kernel.idempotency import find_existing, record
from ..kernel.numbering import next_document_number
from ..kernel.rbac import Role, require_roles
from .models import (
    GoodsReceipt,
    GoodsReceiptCreate,
    GoodsReceiptRead,
    GoodsReceiptRoll,
    GoodsReceiptRollRead,
    PurchaseOrder,
    PurchaseOrderCreate,
    PurchaseOrderLineRead,
    PurchaseOrderRead,
    PurchaseOrderStatus,
)
from .service import (
    ProcurementError,
    build_purchase_order,
    po_total_value,
    post_goods_receipt,
)

router = APIRouter(prefix="/procurement", tags=["procurement"])

_GR_SCOPE = "goods_receipt"


def _po_read(order: PurchaseOrder) -> PurchaseOrderRead:
    return PurchaseOrderRead(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        supplier_id=order.supplier_id,
        sales_order_id=order.sales_order_id,
        currency=order.currency,
        order_date=order.order_date,
        expected_date=order.expected_date,
        notes=order.notes,
        total_value=po_total_value(order),
        lines=[
            PurchaseOrderLineRead(
                id=l.id,
                material_id=l.material_id,
                colour_id=l.colour_id,
                ordered_qty=l.ordered_qty,
                received_qty=l.received_qty,
                unit_price=l.unit_price,
                uom=l.uom,
                outstanding_qty=(l.ordered_qty - l.received_qty),
            )
            for l in order.lines
        ],
    )


# --------------------------------------------------------------------------- #
# Purchase orders (3.1 / 3.2)
# --------------------------------------------------------------------------- #
@router.post("/purchase-orders", response_model=PurchaseOrderRead, status_code=201)
def create_po(
    payload: PurchaseOrderCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.procurement)),
):
    number = next_document_number(session, "PURCHASE_ORDER", "PO")
    order = PurchaseOrder(
        order_number=number,
        status=PurchaseOrderStatus.issued,
        supplier_id=payload.supplier_id,
        sales_order_id=payload.sales_order_id,
        currency=payload.currency,
        order_date=payload.order_date,
        expected_date=payload.expected_date,
        notes=payload.notes,
    )
    session.add(order)
    session.flush()
    try:
        build_purchase_order(session, order, payload)
    except ProcurementError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Invalid reference") from exc
    session.refresh(order)
    return _po_read(order)


@router.get("/purchase-orders", response_model=List[PurchaseOrderRead])
def list_pos(session: Session = Depends(get_session)):
    return [_po_read(o) for o in session.exec(select(PurchaseOrder).order_by(PurchaseOrder.id)).all()]


@router.get("/purchase-orders/{po_id}", response_model=PurchaseOrderRead)
def get_po(po_id: int, session: Session = Depends(get_session)):
    order = session.get(PurchaseOrder, po_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return _po_read(order)


# --------------------------------------------------------------------------- #
# Goods receipt (3.3 / 3.4) — the pivotal transaction
# --------------------------------------------------------------------------- #
def _gr_read(session: Session, receipt: GoodsReceipt) -> GoodsReceiptRead:
    total_length = Decimal("0")
    total_value = Decimal("0")
    rolls_out = []
    lines = {l.id: l for l in session.get(PurchaseOrder, receipt.purchase_order_id).lines}
    for gr_roll in receipt.rolls:
        roll = session.get(Roll, gr_roll.roll_id)
        line = lines.get(gr_roll.purchase_order_line_id)
        total_length += gr_roll.length
        if line is not None:
            total_value += line.unit_price * gr_roll.length
        rolls_out.append(
            GoodsReceiptRollRead(
                roll_id=roll.id,
                roll_number=roll.roll_number,
                purchase_order_line_id=gr_roll.purchase_order_line_id,
                length=gr_roll.length,
                status=roll.status.value,
            )
        )
    return GoodsReceiptRead(
        id=receipt.id,
        receipt_number=receipt.receipt_number,
        purchase_order_id=receipt.purchase_order_id,
        supplier_id=receipt.supplier_id,
        received_date=receipt.received_date,
        total_length=total_length,
        total_value=total_value,
        rolls=rolls_out,
    )


@router.post("/goods-receipts", response_model=GoodsReceiptRead, status_code=201)
def create_goods_receipt(
    payload: GoodsReceiptCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores)),
):
    # Idempotent capture: a replayed client_key returns the original receipt.
    if payload.client_key:
        existing = find_existing(session, _GR_SCOPE, payload.client_key)
        if existing is not None:
            receipt = session.get(GoodsReceipt, existing)
            return _gr_read(session, receipt)
    try:
        receipt = post_goods_receipt(session, payload, actor)
        if payload.client_key:
            record(session, _GR_SCOPE, payload.client_key, receipt.id)
    except ProcurementError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(receipt)
    return _gr_read(session, receipt)


@router.get("/goods-receipts/{gr_id}", response_model=GoodsReceiptRead)
def get_goods_receipt(gr_id: int, session: Session = Depends(get_session)):
    receipt = session.get(GoodsReceipt, gr_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Goods receipt not found")
    return _gr_read(session, receipt)
