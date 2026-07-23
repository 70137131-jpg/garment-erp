"""Automatic accounting integration (blueprint 8.3).

Finance subscribes to operational events and posts their accounting into the
same transaction that emitted them (the active session is read from
:mod:`app.kernel.context`). This is the whole point of the day-one event seam:
operations never import finance, yet every receipt, shipment and invoice books
itself.

Postings:

* ``GoodsReceiptPosted`` → Dr Inventory / Cr Accounts Payable, and raise an AP
  bill for the supplier.
* ``ShipmentDispatched``  → Dr Accounts Receivable / Cr Sales Revenue, and raise
  an AR invoice for the customer.

If the chart of accounts has not been seeded (e.g. a bare test database), the
handlers no-op — operations must never fail because finance is not configured.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlmodel import select

from ..events import GoodsReceiptPosted, ShipmentDispatched
from ..inventory.models import MovementType, StockLedgerEntry
from ..kernel.context import get_current_session
from ..kernel.events import subscribe
from ..kernel.numbering import next_document_number
from ..kernel.types import quantize_money
from ..production.models import CutOrder
from ..sales.models import SalesOrderLine, SalesOrderSizeCell, Shipment, ShipmentLine
from .models import APBill, ARInvoice, JournalSource, SettlementStatus
from .service import (
    ACC_AP,
    ACC_AR,
    ACC_COGS,
    ACC_INVENTORY,
    ACC_SALES,
    account_by_code,
    post_journal,
)


def _finance_ready(session) -> bool:
    return session is not None and account_by_code(session, ACC_AP) is not None


def on_goods_receipt(event: GoodsReceiptPosted) -> None:
    session = get_current_session()
    if not _finance_ready(session) or event.total_value <= 0:
        return
    post_journal(
        session,
        lines=[
            (ACC_INVENTORY, event.total_value, Decimal("0"), "Goods received"),
            (ACC_AP, Decimal("0"), event.total_value, "Payable to supplier"),
        ],
        memo=f"Goods receipt {event.receipt_number}",
        source=JournalSource.system,
        reference_type="goods_receipt",
        reference_id=event.goods_receipt_id,
    )
    bill_number = next_document_number(session, "AP_BILL", "AP")
    session.add(
        APBill(
            bill_number=bill_number,
            supplier_id=event.supplier_id,
            goods_receipt_id=event.goods_receipt_id,
            currency=event.currency,
            amount=event.total_value,
            status=SettlementStatus.open,
            bill_date=date.today(),
        )
    )
    session.flush()


def on_shipment(event: ShipmentDispatched) -> None:
    session = get_current_session()
    if not _finance_ready(session) or event.invoice_value <= 0:
        return
    post_journal(
        session,
        lines=[
            (ACC_AR, event.invoice_value, Decimal("0"), "Customer receivable"),
            (ACC_SALES, Decimal("0"), event.invoice_value, "Sales revenue"),
        ],
        memo=f"Shipment {event.order_number}",
        source=JournalSource.system,
        reference_type="shipment",
        reference_id=event.sales_order_id,
    )
    invoice_number = next_document_number(session, "AR_INVOICE", "INV")
    session.add(
        ARInvoice(
            invoice_number=invoice_number,
            customer_id=event.customer_id,
            sales_order_id=event.sales_order_id,
            shipment_id=event.shipment_id,
            currency=event.currency,
            amount=event.invoice_value,
            status=SettlementStatus.open,
            invoice_date=date.today(),
        )
    )
    # COGS is driven by the same valuation stamped on production issues.  Each
    # partial shipment receives its cumulative share of issued cost, avoiding a
    # second debit when the balance ships later.
    if event.shipment_id is not None:
        shipment = session.get(Shipment, event.shipment_id)
        cut_order_ids = session.exec(
            select(CutOrder.id).where(CutOrder.sales_order_id == event.sales_order_id)
        ).all()
        total_issued_cost = Decimal("0")
        if cut_order_ids:
            value = session.exec(
                select(func.coalesce(func.sum(StockLedgerEntry.extended_cost), 0)).where(
                    StockLedgerEntry.reference_type == "cut_order",
                    StockLedgerEntry.reference_id.in_(cut_order_ids),
                    StockLedgerEntry.movement_type == MovementType.issue,
                )
            ).one()
            total_issued_cost = quantize_money(-Decimal(value))
        # Confirmed order quantity is available from the sales line cells.
        confirmed_qty = session.exec(
            select(func.coalesce(func.sum(SalesOrderSizeCell.confirmed_qty), 0))
            .join(SalesOrderLine, SalesOrderLine.id == SalesOrderSizeCell.line_id)
            .where(SalesOrderLine.sales_order_id == event.sales_order_id)
        ).one()
        prior_cogs = session.exec(
            select(func.coalesce(func.sum(Shipment.cost_of_goods), 0)).where(
                Shipment.sales_order_id == event.sales_order_id,
                Shipment.id != shipment.id,
            )
        ).one()
        cumulative_qty = Decimal(session.exec(
            select(func.coalesce(func.sum(ShipmentLine.quantity), 0))
            .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
            .where(Shipment.sales_order_id == event.sales_order_id)
        ).one())
        target_cogs = quantize_money(
            total_issued_cost * cumulative_qty / Decimal(confirmed_qty)
        ) if confirmed_qty else Decimal("0")
        cogs = quantize_money(max(Decimal("0"), target_cogs - Decimal(prior_cogs)))
        shipment.cost_of_goods = cogs
        session.add(shipment)
        if cogs > 0:
            post_journal(
                session,
                lines=[
                    (ACC_COGS, cogs, Decimal("0"), "Cost of goods sold"),
                    (ACC_INVENTORY, Decimal("0"), cogs, "Inventory consumed"),
                ],
                memo=f"COGS for shipment {shipment.shipment_number}",
                source=JournalSource.system,
                reference_type="shipment_cogs",
                reference_id=shipment.id,
            )
    session.flush()


def register_finance_subscribers() -> None:
    """Wire finance handlers to the dispatcher (idempotent — safe to call again)."""
    subscribe(GoodsReceiptPosted, on_goods_receipt)
    subscribe(ShipmentDispatched, on_shipment)
