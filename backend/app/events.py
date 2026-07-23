"""Domain-event catalog.

Concrete operational events emitted across the system. They live in one module
so emitters (operations) and subscribers (Finance auto-posting, later analytics)
share a single definition — the seam the build-plan calls for on day one. The
dispatcher itself is in :mod:`app.kernel.events`; wiring subscribers happens in
Finance (Phase 9).

Each event carries the minimum a subscriber needs to act *and* the reference
back to the source document for traceability. Money fields are ``Decimal``.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .kernel.events import DomainEvent


@dataclass
class SalesOrderConfirmed(DomainEvent):
    sales_order_id: int
    order_number: str
    customer_id: int
    currency: str
    total_value: Decimal


@dataclass
class GoodsReceiptPosted(DomainEvent):
    goods_receipt_id: int
    receipt_number: str
    supplier_id: int
    purchase_order_id: int
    currency: str
    total_value: Decimal


@dataclass
class ProductionConfirmed(DomainEvent):
    cut_order_id: int
    order_number: str
    style_id: int
    pieces: int
    material_cost: Decimal = field(default=Decimal("0"))


@dataclass
class ShipmentDispatched(DomainEvent):
    sales_order_id: int
    order_number: str
    customer_id: int
    currency: str
    invoice_value: Decimal
    shipment_id: Optional[int] = None


@dataclass
class SupplierInvoiceRaised(DomainEvent):
    """AP trigger — a goods receipt becomes a payable to the supplier."""

    goods_receipt_id: int
    supplier_id: int
    currency: str
    amount: Decimal
    reference: Optional[str] = None
