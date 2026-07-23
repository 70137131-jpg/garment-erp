"""Module 2 — Sales orders with the normalized size matrix.

The size matrix is a hard requirement (blueprint 2.3): a grid of colour × size
where every cell is individually tracked through ordered → confirmed → shipped.
It is stored as **normalized per-size cells** (locked-in decision — never JSON),
so demand and fulfilment are queryable per size.

    SalesOrder → SalesOrderLine (style × colour) → SalesOrderSizeCell (per size)
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin, utcnow
from ..kernel.types import money_field, quantity_field


class SalesOrderStatus(str, Enum):
    draft = "draft"
    confirmed = "confirmed"
    in_production = "in_production"
    partially_shipped = "partially_shipped"
    shipped = "shipped"
    closed = "closed"
    cancelled = "cancelled"


# --------------------------------------------------------------------------- #
# 2.1 Header
# --------------------------------------------------------------------------- #
class SalesOrderBase(SQLModel):
    customer_id: int = Field(foreign_key="customer.id", index=True)
    customer_po_number: Optional[str] = None
    season_id: Optional[int] = Field(default=None, foreign_key="season.id")
    order_date: Optional[date] = None
    currency: str = "USD"
    incoterms: Optional[str] = None
    payment_terms: Optional[str] = None
    notes: Optional[str] = None


class SalesOrder(SalesOrderBase, TimestampMixin, table=True):
    __tablename__ = "sales_order"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_number: str = Field(index=True, unique=True)
    status: SalesOrderStatus = Field(default=SalesOrderStatus.draft, index=True)

    lines: List["SalesOrderLine"] = Relationship(
        back_populates="order",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


# --------------------------------------------------------------------------- #
# 2.2 Lines by style & colour
# --------------------------------------------------------------------------- #
class SalesOrderLine(SQLModel, table=True):
    __tablename__ = "sales_order_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    sales_order_id: int = Field(foreign_key="sales_order.id", index=True)
    style_id: int = Field(foreign_key="style.id", index=True)
    colour_id: int = Field(foreign_key="colour.id", index=True)
    unit_price: Decimal = money_field(default=Decimal("0"))
    delivery_date: Optional[date] = None
    destination: Optional[str] = None
    packing_ratio: Optional[str] = None

    order: Optional[SalesOrder] = Relationship(back_populates="lines")
    cells: List["SalesOrderSizeCell"] = Relationship(
        back_populates="line",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "SalesOrderSizeCell.position",
        },
    )


# --------------------------------------------------------------------------- #
# 2.3 The size matrix — one tracked cell per size
# --------------------------------------------------------------------------- #
class SalesOrderSizeCell(SQLModel, table=True):
    __tablename__ = "sales_order_size_cell"

    id: Optional[int] = Field(default=None, primary_key=True)
    line_id: int = Field(foreign_key="sales_order_line.id", index=True)
    size_range_item_id: int = Field(foreign_key="size_range_item.id")
    position: int
    size_label: str
    ordered_qty: int = 0
    confirmed_qty: int = 0
    shipped_qty: int = 0

    line: Optional[SalesOrderLine] = Relationship(back_populates="cells")


class ShipmentStatus(str, Enum):
    dispatched = "dispatched"
    cancelled = "cancelled"


class Shipment(TimestampMixin, table=True):
    """An immutable dispatch document.  An order can have many shipments."""

    __tablename__ = "shipment"

    id: Optional[int] = Field(default=None, primary_key=True)
    shipment_number: str = Field(index=True, unique=True)
    sales_order_id: int = Field(foreign_key="sales_order.id", index=True)
    shipment_date: Optional[date] = None
    shipping_reference: Optional[str] = Field(default=None, index=True)
    destination: Optional[str] = None
    notes: Optional[str] = None
    status: ShipmentStatus = Field(default=ShipmentStatus.dispatched, index=True)
    cost_of_goods: Decimal = money_field(default=Decimal("0"))


class ShipmentLine(SQLModel, table=True):
    __tablename__ = "shipment_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    shipment_id: int = Field(foreign_key="shipment.id", index=True)
    sales_order_line_id: int = Field(foreign_key="sales_order_line.id", index=True)
    sales_order_size_cell_id: int = Field(foreign_key="sales_order_size_cell.id", index=True)
    quantity: int = 0
    carton_count: int = 0


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class SizeCellInput(SQLModel):
    size_label: str
    ordered_qty: int


class SalesOrderLineInput(SQLModel):
    style_id: int
    colour_id: int
    unit_price: Decimal = Decimal("0")
    delivery_date: Optional[date] = None
    destination: Optional[str] = None
    packing_ratio: Optional[str] = None
    sizes: List[SizeCellInput] = []


class SalesOrderCreate(SalesOrderBase):
    lines: List[SalesOrderLineInput] = []


class SizeCellRead(SQLModel):
    id: int
    size_label: str
    position: int
    ordered_qty: int
    confirmed_qty: int
    shipped_qty: int


class SalesOrderLineRead(SQLModel):
    id: int
    style_id: int
    colour_id: int
    unit_price: Decimal
    delivery_date: Optional[date]
    destination: Optional[str]
    packing_ratio: Optional[str]
    line_quantity: int
    line_value: Decimal
    sizes: List[SizeCellRead]


class SalesOrderRead(SalesOrderBase):
    id: int
    order_number: str
    status: SalesOrderStatus
    total_quantity: int
    total_shipped_quantity: int
    total_value: Decimal
    lines: List[SalesOrderLineRead]


class SalesOrderRevision(SQLModel, table=True):
    __tablename__ = "sales_order_revision"

    id: Optional[int] = Field(default=None, primary_key=True)
    sales_order_id: int = Field(foreign_key="sales_order.id", index=True)
    revision_no: int
    action: str = Field(index=True)
    reason: str
    snapshot_json: str
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    created_by: Optional[str] = None


# --------------------------------------------------------------------------- #
# Material planning snapshot
# --------------------------------------------------------------------------- #
class SalesOrderMaterialRequirement(SQLModel, table=True):
    """The explodable, auditable material plan for one sales order.

    Rows are regenerated when an order is confirmed.  Persisting the result is
    intentional: procurement must be able to see the exact shortage that led to
    a requisition even if stock changes later in the day.
    """

    __tablename__ = "sales_order_material_requirement"

    id: Optional[int] = Field(default=None, primary_key=True)
    sales_order_id: int = Field(foreign_key="sales_order.id", index=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    required_qty: Decimal = quantity_field(default=Decimal("0"))
    available_qty: Decimal = quantity_field(default=Decimal("0"))
    shortage_qty: Decimal = quantity_field(default=Decimal("0"))
    generated_at: datetime = Field(default_factory=utcnow, nullable=False)


class SalesOrderRevisionRead(SQLModel):
    id: int
    sales_order_id: int
    revision_no: int
    action: str
    reason: str
    snapshot_json: str
    created_at: datetime
    created_by: Optional[str]


class SalesOrderAmendRequest(SQLModel):
    customer_po_number: Optional[str] = None
    order_date: Optional[date] = None
    incoterms: Optional[str] = None
    payment_terms: Optional[str] = None
    notes: Optional[str] = None
    lines: Optional[List[SalesOrderLineInput]] = None
    reason: str


class SalesOrderCancelRequest(SQLModel):
    reason: str


class SalesOrderCloseRequest(SQLModel):
    reason: str


class ShipmentLineInput(SQLModel):
    sales_order_size_cell_id: int
    quantity: int
    carton_count: int = 0


class ShipmentCreate(SQLModel):
    shipment_date: Optional[date] = None
    shipping_reference: Optional[str] = None
    destination: Optional[str] = None
    notes: Optional[str] = None
    # Omitted/empty preserves the former full-shipment API behaviour.
    lines: List[ShipmentLineInput] = []


class ShipmentLineRead(SQLModel):
    id: int
    sales_order_line_id: int
    sales_order_size_cell_id: int
    size_label: str
    quantity: int
    carton_count: int


class ShipmentRead(SQLModel):
    id: int
    shipment_number: str
    sales_order_id: int
    shipment_date: Optional[date]
    shipping_reference: Optional[str]
    destination: Optional[str]
    notes: Optional[str]
    status: ShipmentStatus
    cost_of_goods: Decimal
    total_quantity: int
    total_cartons: int
    lines: List[ShipmentLineRead]


class CommercialCheckRead(SQLModel):
    eligible: bool
    order_value: Decimal
    credit_limit: Decimal
    current_exposure: Decimal
    projected_exposure: Decimal
    messages: List[str] = []


class MaterialRequirementRead(SQLModel):
    id: int
    sales_order_id: int
    material_id: int
    required_qty: Decimal
    available_qty: Decimal
    shortage_qty: Decimal
    generated_at: datetime
