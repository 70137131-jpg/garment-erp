"""Module 3 — Procurement: purchase orders and goods receipt.

Goods receipt is the pivotal event (build-plan Phase 4): in one transaction it
advances the PO, posts ledger movements, creates a roll per physical roll, and
emits ``GoodsReceiptPosted``. The models here are the durable record; the atomic
work lives in :mod:`app.procurement.service`.

Quantities on PO lines are held in the material's **base UoM** (metres) so
received-vs-ordered compares cleanly against roll lengths and the stock ledger.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin, utcnow
from ..kernel.types import money_field, quantity_field


class PurchaseOrderStatus(str, Enum):
    draft = "draft"
    pending_approval = "pending_approval"
    issued = "issued"
    rejected = "rejected"
    partially_received = "partially_received"
    received = "received"
    closed = "closed"
    cancelled = "cancelled"


class PurchaseRequisitionStatus(str, Enum):
    draft = "draft"
    partially_ordered = "partially_ordered"
    ordered = "ordered"
    cancelled = "cancelled"


# --------------------------------------------------------------------------- #
# 3.1 / 3.2 Purchase order
# --------------------------------------------------------------------------- #
class PurchaseOrderBase(SQLModel):
    supplier_id: int = Field(foreign_key="supplier.id", index=True)
    sales_order_id: Optional[int] = Field(
        default=None, foreign_key="sales_order.id"
    )
    currency: str = "USD"
    order_date: Optional[date] = None
    expected_date: Optional[date] = None
    notes: Optional[str] = None


class PurchaseOrder(PurchaseOrderBase, TimestampMixin, table=True):
    __tablename__ = "purchase_order"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_number: str = Field(index=True, unique=True)
    status: PurchaseOrderStatus = Field(
        default=PurchaseOrderStatus.draft, index=True
    )
    requires_approval: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    lines: List["PurchaseOrderLine"] = Relationship(
        back_populates="order",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class PurchaseOrderLine(SQLModel, table=True):
    __tablename__ = "purchase_order_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    purchase_order_id: int = Field(foreign_key="purchase_order.id", index=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    colour_id: Optional[int] = Field(default=None, foreign_key="colour.id")

    ordered_qty: Decimal = quantity_field(default=Decimal("0"))  # base UoM
    received_qty: Decimal = quantity_field(default=Decimal("0"))  # base UoM
    unit_price: Decimal = money_field(default=Decimal("0"))  # per base UoM
    uom: str = "metre"

    # 3.2 fabric/trim ordering spec (free-text is fine for MVP).
    construction: Optional[str] = None
    composition: Optional[str] = None
    width_cm: Optional[Decimal] = quantity_field(default=None, nullable=True)
    gsm: Optional[Decimal] = quantity_field(default=None, nullable=True)
    finish: Optional[str] = None
    inspection_standard: Optional[str] = None

    order: Optional[PurchaseOrder] = Relationship(back_populates="lines")


# --------------------------------------------------------------------------- #
# 3.3 Goods receipt
# --------------------------------------------------------------------------- #
class GoodsReceipt(TimestampMixin, table=True):
    __tablename__ = "goods_receipt"

    id: Optional[int] = Field(default=None, primary_key=True)
    receipt_number: str = Field(index=True, unique=True)
    purchase_order_id: int = Field(foreign_key="purchase_order.id", index=True)
    supplier_id: int = Field(foreign_key="supplier.id", index=True)
    received_date: Optional[date] = None
    note: Optional[str] = None

    rolls: List["GoodsReceiptRoll"] = Relationship(
        back_populates="goods_receipt",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class GoodsReceiptRoll(SQLModel, table=True):
    """Link between a receipt and the roll it created (audit of what arrived)."""

    __tablename__ = "goods_receipt_roll"

    id: Optional[int] = Field(default=None, primary_key=True)
    goods_receipt_id: int = Field(foreign_key="goods_receipt.id", index=True)
    purchase_order_line_id: int = Field(
        foreign_key="purchase_order_line.id", index=True
    )
    roll_id: int = Field(foreign_key="roll.id", index=True)
    length: Decimal = quantity_field(default=Decimal("0"))

    goods_receipt: Optional[GoodsReceipt] = Relationship(back_populates="rolls")


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class PurchaseOrderLineInput(SQLModel):
    material_id: int
    colour_id: Optional[int] = None
    ordered_qty: Decimal
    unit_price: Decimal = Decimal("0")
    uom: str = "metre"
    construction: Optional[str] = None
    composition: Optional[str] = None
    width_cm: Optional[Decimal] = None
    gsm: Optional[Decimal] = None
    finish: Optional[str] = None
    inspection_standard: Optional[str] = None


class PurchaseOrderCreate(PurchaseOrderBase):
    requires_approval: bool = False
    lines: List[PurchaseOrderLineInput] = []


class PurchaseOrderLineRead(SQLModel):
    id: int
    material_id: int
    colour_id: Optional[int]
    ordered_qty: Decimal
    received_qty: Decimal
    unit_price: Decimal
    uom: str
    outstanding_qty: Decimal


class PurchaseOrderRead(PurchaseOrderBase):
    id: int
    order_number: str
    status: PurchaseOrderStatus
    total_value: Decimal
    requires_approval: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    lines: List[PurchaseOrderLineRead]


class ReceiptRollInput(SQLModel):
    purchase_order_line_id: int
    roll_number: Optional[str] = None  # auto-generated if omitted
    length: Decimal
    weight: Optional[Decimal] = None
    width_cm: Optional[Decimal] = None
    gsm: Optional[Decimal] = None
    dye_lot: Optional[str] = None
    shade_code: Optional[str] = None
    shade_group: Optional[str] = None
    grade: str = "A"
    warehouse: str = "MAIN"
    location: Optional[str] = None


class GoodsReceiptCreate(SQLModel):
    purchase_order_id: int
    received_date: Optional[date] = None
    note: Optional[str] = None
    client_key: Optional[str] = None  # idempotency
    rolls: List[ReceiptRollInput] = []


class GoodsReceiptRollRead(SQLModel):
    roll_id: int
    roll_number: str
    purchase_order_line_id: int
    length: Decimal
    status: str


class GoodsReceiptRead(SQLModel):
    id: int
    receipt_number: str
    purchase_order_id: int
    supplier_id: int
    received_date: Optional[date]
    total_length: Decimal
    total_value: Decimal
    rolls: List[GoodsReceiptRollRead]


class PurchaseOrderRevision(SQLModel, table=True):
    __tablename__ = "purchase_order_revision"

    id: Optional[int] = Field(default=None, primary_key=True)
    purchase_order_id: int = Field(foreign_key="purchase_order.id", index=True)
    revision_no: int
    action: str = Field(index=True)
    reason: str
    snapshot_json: str
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    created_by: Optional[str] = None


# --------------------------------------------------------------------------- #
# Purchase requisitions — demand before a supplier-specific PO exists.
# --------------------------------------------------------------------------- #
class PurchaseRequisition(TimestampMixin, table=True):
    __tablename__ = "purchase_requisition"

    id: Optional[int] = Field(default=None, primary_key=True)
    requisition_number: str = Field(index=True, unique=True)
    sales_order_id: Optional[int] = Field(default=None, foreign_key="sales_order.id", index=True)
    suggested_supplier_id: Optional[int] = Field(default=None, foreign_key="supplier.id", index=True)
    requested_date: Optional[date] = None
    status: PurchaseRequisitionStatus = Field(default=PurchaseRequisitionStatus.draft, index=True)
    notes: Optional[str] = None


class PurchaseRequisitionLine(SQLModel, table=True):
    __tablename__ = "purchase_requisition_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    purchase_requisition_id: int = Field(foreign_key="purchase_requisition.id", index=True)
    material_requirement_id: Optional[int] = Field(
        default=None, foreign_key="sales_order_material_requirement.id", index=True
    )
    material_id: int = Field(foreign_key="material.id", index=True)
    requested_qty: Decimal = quantity_field(default=Decimal("0"))
    ordered_qty: Decimal = quantity_field(default=Decimal("0"))
    uom: str = "metre"
    note: Optional[str] = None


class PurchaseOrderRequisitionLine(SQLModel, table=True):
    """Allocation link that lets one PO line cover several requisition lines."""

    __tablename__ = "purchase_order_requisition_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    purchase_order_line_id: int = Field(foreign_key="purchase_order_line.id", index=True)
    purchase_requisition_line_id: int = Field(
        foreign_key="purchase_requisition_line.id", index=True
    )
    allocated_qty: Decimal = quantity_field(default=Decimal("0"))


class PurchaseOrderRevisionRead(SQLModel):
    id: int
    purchase_order_id: int
    revision_no: int
    action: str
    reason: str
    snapshot_json: str
    created_at: datetime
    created_by: Optional[str]


class PurchaseOrderAmendRequest(SQLModel):
    expected_date: Optional[date] = None
    notes: Optional[str] = None
    lines: Optional[List[PurchaseOrderLineInput]] = None
    reason: str


class PurchaseRequisitionLineInput(SQLModel):
    material_id: int
    requested_qty: Decimal
    uom: str = "metre"
    note: Optional[str] = None


class PurchaseRequisitionCreate(SQLModel):
    sales_order_id: Optional[int] = None
    suggested_supplier_id: Optional[int] = None
    requested_date: Optional[date] = None
    notes: Optional[str] = None
    lines: List[PurchaseRequisitionLineInput]


class PurchaseRequisitionLineRead(SQLModel):
    id: int
    material_requirement_id: Optional[int]
    material_id: int
    requested_qty: Decimal
    ordered_qty: Decimal
    outstanding_qty: Decimal
    uom: str
    note: Optional[str]


class PurchaseRequisitionRead(SQLModel):
    id: int
    requisition_number: str
    sales_order_id: Optional[int]
    suggested_supplier_id: Optional[int]
    requested_date: Optional[date]
    status: PurchaseRequisitionStatus
    notes: Optional[str]
    lines: List[PurchaseRequisitionLineRead]


class RequisitionPoLineInput(PurchaseOrderLineInput):
    requisition_line_ids: List[int]


class PurchaseOrderFromRequisitions(SQLModel):
    supplier_id: int
    currency: str = "USD"
    order_date: Optional[date] = None
    expected_date: Optional[date] = None
    notes: Optional[str] = None
    requires_approval: bool = False
    lines: List[RequisitionPoLineInput]


class PurchaseOrderApprovalRequest(SQLModel):
    approved: bool
    comment: str


class SupplierPerformanceRead(SQLModel):
    supplier_id: int
    purchase_orders: int
    receipts: int
    ordered_value: Decimal
    ordered_qty: Decimal
    received_qty: Decimal
    fulfilment_pct: Decimal
    on_time_receipts: int
    on_time_pct: Decimal
    accepted_rolls: int
    rejected_rolls: int
    acceptance_pct: Decimal
