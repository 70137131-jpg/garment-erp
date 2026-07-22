"""Module 2 — Sales orders with the normalized size matrix.

The size matrix is a hard requirement (blueprint 2.3): a grid of colour × size
where every cell is individually tracked through ordered → confirmed → shipped.
It is stored as **normalized per-size cells** (locked-in decision — never JSON),
so demand and fulfilment are queryable per size.

    SalesOrder → SalesOrderLine (style × colour) → SalesOrderSizeCell (per size)
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import money_field, quantity_field


class SalesOrderStatus(str, Enum):
    draft = "draft"
    confirmed = "confirmed"
    in_production = "in_production"
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
    total_value: Decimal
    lines: List[SalesOrderLineRead]
