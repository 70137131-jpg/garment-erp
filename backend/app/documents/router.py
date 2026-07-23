from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import Session

from ..db import get_session
from ..finance.models import ARInvoice
from ..inventory.models import Roll
from ..masters.models import Customer, Material, Supplier
from ..procurement.models import GoodsReceipt, PurchaseOrder
from ..production.models import CutOrder, SewingOrder
from ..sales.models import SalesOrder
from .pdf import build_document


router = APIRouter(prefix="/documents", tags=["documents"])


def _response(payload: bytes, filename: str) -> Response:
    return Response(
        payload,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/purchase-orders/{po_id}/pdf")
def purchase_order_pdf(po_id: int, session: Session = Depends(get_session)):
    order = session.get(PurchaseOrder, po_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    supplier = session.get(Supplier, order.supplier_id)
    rows = []
    total = Decimal("0")
    for line in order.lines:
        material = session.get(Material, line.material_id)
        value = line.ordered_qty * line.unit_price
        total += value
        rows.append((material.code if material else line.material_id, material.name if material else "Material", line.ordered_qty, line.uom, line.unit_price, value))
    payload = build_document(
        title="Purchase Order", number=order.order_number, status=order.status.value,
        metadata=[
            ("Supplier", supplier.name if supplier else order.supplier_id),
            ("Supplier code", supplier.code if supplier else "-"),
            ("Order date", order.order_date), ("Expected date", order.expected_date),
            ("Currency", order.currency), ("Payment terms", supplier.payment_terms if supplier else None),
        ],
        columns=("Material", "Description", "Quantity", "UoM", "Unit price", "Line value"),
        rows=rows,
        totals=((f"Total {order.currency}", total),),
        notes=order.notes,
    )
    return _response(payload, f"{order.order_number}.pdf")


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, session: Session = Depends(get_session)):
    invoice = session.get(ARInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = session.get(Customer, invoice.customer_id)
    order = session.get(SalesOrder, invoice.sales_order_id) if invoice.sales_order_id else None
    rows = []
    if order:
        for line in order.lines:
            quantity = sum(cell.shipped_qty or cell.confirmed_qty for cell in line.cells)
            rows.append((line.style_id, line.colour_id, quantity, line.unit_price, line.unit_price * quantity))
    if not rows:
        rows.append(("Sales", "Invoice", 1, invoice.amount, invoice.amount))
    payload = build_document(
        title="Sales Invoice", number=invoice.invoice_number, status=invoice.status.value,
        metadata=[
            ("Customer", customer.name if customer else invoice.customer_id),
            ("Customer code", customer.code if customer else "-"),
            ("Invoice date", invoice.invoice_date), ("Due date", invoice.due_date),
            ("Currency", invoice.currency), ("Sales order", order.order_number if order else "-"),
            ("Billing address", customer.billing_address if customer else None),
        ],
        columns=("Style", "Colour", "Quantity", "Unit price", "Line value"),
        rows=rows,
        totals=((f"Invoice total {invoice.currency}", invoice.amount), ("Outstanding", invoice.amount - invoice.settled_amount)),
    )
    return _response(payload, f"{invoice.invoice_number}.pdf")


@router.get("/goods-receipts/{receipt_id}/pdf")
def goods_receipt_pdf(receipt_id: int, session: Session = Depends(get_session)):
    receipt = session.get(GoodsReceipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Goods receipt not found")
    supplier = session.get(Supplier, receipt.supplier_id)
    order = session.get(PurchaseOrder, receipt.purchase_order_id)
    rows = []
    total_length = Decimal("0")
    for line in receipt.rolls:
        roll = session.get(Roll, line.roll_id)
        total_length += line.length
        rows.append((roll.roll_number if roll else line.roll_id, roll.material_id if roll else "-", line.length, roll.dye_lot if roll else "-", roll.grade.value if roll else "-", roll.status.value if roll else "-"))
    payload = build_document(
        title="Goods Receipt", number=receipt.receipt_number, status="Posted",
        metadata=[
            ("Supplier", supplier.name if supplier else receipt.supplier_id),
            ("Purchase order", order.order_number if order else receipt.purchase_order_id),
            ("Received date", receipt.received_date), ("Roll count", len(receipt.rolls)),
        ],
        columns=("Roll", "Material ID", "Length", "Dye lot", "Grade", "Status"),
        rows=rows,
        totals=(("Total received length", total_length),),
        notes=receipt.note,
    )
    return _response(payload, f"{receipt.receipt_number}.pdf")


@router.get("/cut-orders/{cut_id}/pdf")
def cut_order_pdf(cut_id: int, session: Session = Depends(get_session)):
    order = session.get(CutOrder, cut_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Cut order not found")
    payload = build_document(
        title="Cut Order", number=order.order_number, status=order.status.value,
        metadata=[
            ("Style ID", order.style_id), ("Colour ID", order.colour_id),
            ("Sales order ID", order.sales_order_id), ("BOM version ID", order.bom_version_id),
            ("Fabric material ID", order.fabric_material_id), ("Marker efficiency", order.marker_efficiency),
        ],
        columns=("Size", "Planned quantity", "Cut quantity"),
        rows=[(size.size_label, size.planned_qty, size.cut_qty) for size in order.sizes],
        totals=(("Fabric required", order.fabric_required), ("Fabric issued", order.fabric_issued), ("Pieces cut", order.pieces_cut)),
    )
    return _response(payload, f"{order.order_number}.pdf")


@router.get("/sewing-orders/{sewing_id}/pdf")
def sewing_order_pdf(sewing_id: int, session: Session = Depends(get_session)):
    order = session.get(SewingOrder, sewing_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sewing order not found")
    payload = build_document(
        title="Sewing Work Order", number=order.order_number, status=order.status.value,
        metadata=[
            ("Style ID", order.style_id), ("Cut order ID", order.cut_order_id),
            ("Production line", order.line), ("SAM", order.sam),
        ],
        columns=("Date", "Shift", "Produced", "Operators", "Minutes", "Efficiency %"),
        rows=[(row.output_date, row.shift, row.produced_qty, row.operators, row.working_minutes, row.efficiency_pct) for row in order.daily_outputs],
        totals=(("Planned quantity", order.planned_qty), ("Produced quantity", order.produced_qty)),
    )
    return _response(payload, f"{order.order_number}.pdf")
