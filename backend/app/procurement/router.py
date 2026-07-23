from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..inventory.models import Roll
from ..kernel.idempotency import find_existing, record
from ..kernel.numbering import next_document_number
from ..kernel.query import csv_download, page_bounds
from ..kernel.rbac import Role, require_roles
from ..kernel.audit import utcnow
from .models import (
    GoodsReceipt,
    GoodsReceiptCreate,
    GoodsReceiptRead,
    GoodsReceiptRoll,
    GoodsReceiptRollRead,
    PurchaseOrder,
    PurchaseOrderAmendRequest,
    PurchaseOrderApprovalRequest,
    PurchaseOrderCreate,
    PurchaseOrderLineRead,
    PurchaseOrderRead,
    PurchaseOrderFromRequisitions,
    PurchaseOrderRevision,
    PurchaseOrderRevisionRead,
    PurchaseOrderStatus,
    PurchaseRequisition,
    PurchaseRequisitionCreate,
    PurchaseRequisitionLine,
    PurchaseRequisitionLineRead,
    PurchaseRequisitionRead,
    PurchaseRequisitionStatus,
    SupplierPerformanceRead,
)
from .service import (
    ProcurementError,
    amend_purchase_order,
    build_purchase_order,
    create_purchase_order_from_requisitions,
    create_purchase_requisition,
    create_requisition_from_shortages,
    po_total_value,
    post_goods_receipt,
    record_po_revision,
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
        requires_approval=order.requires_approval,
        approved_by=order.approved_by,
        approved_at=order.approved_at,
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
        status=(
            PurchaseOrderStatus.pending_approval
            if payload.requires_approval
            else PurchaseOrderStatus.issued
        ),
        requires_approval=payload.requires_approval,
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
def list_pos(
    q: Optional[str] = None,
    status: Optional[PurchaseOrderStatus] = None,
    supplier_id: Optional[int] = None,
    sort: str = "id",
    direction: str = "desc",
    offset: int = 0,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    offset, limit = page_bounds(offset, limit)
    sort_fields = {
        "id": PurchaseOrder.id,
        "order_number": PurchaseOrder.order_number,
        "order_date": PurchaseOrder.order_date,
        "expected_date": PurchaseOrder.expected_date,
        "status": PurchaseOrder.status,
    }
    if sort not in sort_fields or direction not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail="Invalid sort field or direction")
    stmt = select(PurchaseOrder)
    if q:
        stmt = stmt.where(func.lower(PurchaseOrder.order_number).like(f"%{q.strip().lower()}%"))
    if status is not None:
        stmt = stmt.where(PurchaseOrder.status == status)
    if supplier_id is not None:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    field = sort_fields[sort]
    stmt = stmt.order_by(field.desc() if direction == "desc" else field.asc())
    return [_po_read(order) for order in session.exec(stmt.offset(offset).limit(limit)).all()]


@router.get("/purchase-orders/export")
def export_purchase_orders(session: Session = Depends(get_session)):
    orders = session.exec(select(PurchaseOrder).order_by(PurchaseOrder.id.desc())).all()
    return csv_download(
        "purchase-orders.csv",
        [
            ("number", "PO"), ("status", "Status"), ("supplier", "Supplier ID"),
            ("order_date", "Order Date"), ("expected", "Expected Date"),
            ("currency", "Currency"), ("value", "Value"),
        ],
        [
            {
                "number": order.order_number, "status": order.status.value,
                "supplier": order.supplier_id, "order_date": order.order_date,
                "expected": order.expected_date, "currency": order.currency,
                "value": po_total_value(order),
            }
            for order in orders
        ],
    )


def _requisition_read(session: Session, requisition: PurchaseRequisition) -> PurchaseRequisitionRead:
    lines = session.exec(
        select(PurchaseRequisitionLine)
        .where(PurchaseRequisitionLine.purchase_requisition_id == requisition.id)
        .order_by(PurchaseRequisitionLine.id)
    ).all()
    return PurchaseRequisitionRead(
        id=requisition.id,
        requisition_number=requisition.requisition_number,
        sales_order_id=requisition.sales_order_id,
        suggested_supplier_id=requisition.suggested_supplier_id,
        requested_date=requisition.requested_date,
        status=requisition.status,
        notes=requisition.notes,
        lines=[
            PurchaseRequisitionLineRead(
                id=line.id,
                material_requirement_id=line.material_requirement_id,
                material_id=line.material_id,
                requested_qty=line.requested_qty,
                ordered_qty=line.ordered_qty,
                outstanding_qty=line.requested_qty - line.ordered_qty,
                uom=line.uom,
                note=line.note,
            )
            for line in lines
        ],
    )


# --------------------------------------------------------------------------- #
# Purchase requisitions (PR-02) and consolidated PO conversion (PR-03)
# --------------------------------------------------------------------------- #
@router.post("/purchase-requisitions", response_model=PurchaseRequisitionRead, status_code=201)
def create_requisition(
    payload: PurchaseRequisitionCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.procurement, Role.planner)),
):
    try:
        requisition = create_purchase_requisition(session, payload, actor)
    except ProcurementError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _requisition_read(session, requisition)


@router.post("/purchase-requisitions/from-sales-orders/{sales_order_id}", response_model=PurchaseRequisitionRead, status_code=201)
def create_requisition_from_order_shortages(
    sales_order_id: int,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.procurement, Role.planner)),
):
    try:
        requisition = create_requisition_from_shortages(session, sales_order_id, actor)
    except ProcurementError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _requisition_read(session, requisition)


@router.get("/purchase-requisitions", response_model=List[PurchaseRequisitionRead])
def list_requisitions(
    status: Optional[PurchaseRequisitionStatus] = None,
    sales_order_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    stmt = select(PurchaseRequisition).order_by(PurchaseRequisition.id.desc())
    if status is not None:
        stmt = stmt.where(PurchaseRequisition.status == status)
    if sales_order_id is not None:
        stmt = stmt.where(PurchaseRequisition.sales_order_id == sales_order_id)
    return [_requisition_read(session, requisition) for requisition in session.exec(stmt).all()]


@router.get("/purchase-requisitions/{requisition_id}", response_model=PurchaseRequisitionRead)
def get_requisition(requisition_id: int, session: Session = Depends(get_session)):
    requisition = session.get(PurchaseRequisition, requisition_id)
    if requisition is None:
        raise HTTPException(status_code=404, detail="Purchase requisition not found")
    return _requisition_read(session, requisition)


@router.post("/purchase-orders/from-requisitions", response_model=PurchaseOrderRead, status_code=201)
def create_po_from_requisitions(
    payload: PurchaseOrderFromRequisitions,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.procurement)),
):
    try:
        order = create_purchase_order_from_requisitions(session, payload)
    except ProcurementError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _po_read(order)


@router.get("/purchase-orders/{po_id}", response_model=PurchaseOrderRead)
def get_po(po_id: int, session: Session = Depends(get_session)):
    order = session.get(PurchaseOrder, po_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return _po_read(order)


@router.get("/purchase-orders/{po_id}/history", response_model=List[PurchaseOrderRevisionRead])
def po_history(po_id: int, session: Session = Depends(get_session)):
    if session.get(PurchaseOrder, po_id) is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return session.exec(
        select(PurchaseOrderRevision)
        .where(PurchaseOrderRevision.purchase_order_id == po_id)
        .order_by(PurchaseOrderRevision.revision_no.desc())
    ).all()


@router.post("/purchase-orders/{po_id}/approval", response_model=PurchaseOrderRead)
def decide_po(
    po_id: int,
    payload: PurchaseOrderApprovalRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.procurement, Role.admin)),
):
    order = session.get(PurchaseOrder, po_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if order.status != PurchaseOrderStatus.pending_approval:
        raise HTTPException(status_code=409, detail="Purchase order is not pending approval")
    try:
        record_po_revision(
            session,
            order,
            action="approved" if payload.approved else "rejected",
            reason=payload.comment,
            actor=actor,
        )
    except ProcurementError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    order.status = PurchaseOrderStatus.issued if payload.approved else PurchaseOrderStatus.rejected
    order.approved_by = actor if payload.approved else None
    order.approved_at = utcnow() if payload.approved else None
    session.add(order)
    session.commit()
    session.refresh(order)
    return _po_read(order)


@router.post("/purchase-orders/{po_id}/amend", response_model=PurchaseOrderRead)
def amend_po(
    po_id: int,
    payload: PurchaseOrderAmendRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.procurement)),
):
    order = session.get(PurchaseOrder, po_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    try:
        amend_purchase_order(session, order, payload, actor)
    except ProcurementError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(order)
    return _po_read(order)


@router.get("/supplier-performance", response_model=List[SupplierPerformanceRead])
def supplier_performance(session: Session = Depends(get_session)):
    supplier_ids = session.exec(select(PurchaseOrder.supplier_id).distinct()).all()
    output = []
    for supplier_id in supplier_ids:
        orders = session.exec(select(PurchaseOrder).where(PurchaseOrder.supplier_id == supplier_id)).all()
        receipts = session.exec(select(GoodsReceipt).where(GoodsReceipt.supplier_id == supplier_id)).all()
        ordered_qty = sum((line.ordered_qty for order in orders for line in order.lines), Decimal("0"))
        received_qty = sum((line.received_qty for order in orders for line in order.lines), Decimal("0"))
        on_time = 0
        accepted = 0
        rejected = 0
        for receipt in receipts:
            order = session.get(PurchaseOrder, receipt.purchase_order_id)
            if order and order.expected_date and receipt.received_date and receipt.received_date <= order.expected_date:
                on_time += 1
            for receipt_roll in receipt.rolls:
                roll = session.get(Roll, receipt_roll.roll_id)
                if roll and roll.status in ("rejected", "quarantined"):
                    rejected += 1
                else:
                    accepted += 1
        receipt_count = len(receipts)
        roll_count = accepted + rejected
        output.append(SupplierPerformanceRead(
            supplier_id=supplier_id,
            purchase_orders=len(orders),
            receipts=receipt_count,
            ordered_value=sum((po_total_value(order) for order in orders), Decimal("0")),
            ordered_qty=ordered_qty,
            received_qty=received_qty,
            fulfilment_pct=(received_qty / ordered_qty * 100).quantize(Decimal("0.01")) if ordered_qty else Decimal("0"),
            on_time_receipts=on_time,
            on_time_pct=(Decimal(on_time) / Decimal(receipt_count) * 100).quantize(Decimal("0.01")) if receipt_count else Decimal("0"),
            accepted_rolls=accepted,
            rejected_rolls=rejected,
            acceptance_pct=(Decimal(accepted) / Decimal(roll_count) * 100).quantize(Decimal("0.01")) if roll_count else Decimal("0"),
        ))
    return output


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


@router.get("/goods-receipts", response_model=List[GoodsReceiptRead])
def list_goods_receipts(session: Session = Depends(get_session)):
    receipts = session.exec(
        select(GoodsReceipt).order_by(GoodsReceipt.id.desc())
    ).all()
    return [_gr_read(session, receipt) for receipt in receipts]


@router.get("/goods-receipts/{gr_id}", response_model=GoodsReceiptRead)
def get_goods_receipt(gr_id: int, session: Session = Depends(get_session)):
    receipt = session.get(GoodsReceipt, gr_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Goods receipt not found")
    return _gr_read(session, receipt)
