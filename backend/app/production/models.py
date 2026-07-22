"""Module 5 — Production: cut orders, roll issue, sewing orders, daily
efficiency, and subcontracting.

The cut order is where the size-aware BOM pays off: fabric requirement is
computed as ``Σ consumption(size) × qty(size) × (1 + wastage)`` against a
*specific* approved ``bom_version`` (5.2). Issuing rolls consumes stock through
the immutable ledger (5.3) and consumes the matching reservations. Completing a
cut order emits ``ProductionConfirmed``.
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin, utcnow
from ..kernel.types import money_field, quantity_field, rate_field


class CutOrderStatus(str, Enum):
    planned = "planned"
    fabric_reserved = "fabric_reserved"
    in_cutting = "in_cutting"
    completed = "completed"
    cancelled = "cancelled"


class SewingOrderStatus(str, Enum):
    planned = "planned"
    active = "active"
    completed = "completed"
    closed = "closed"


class SubcontractStatus(str, Enum):
    open = "open"
    partially_received = "partially_received"
    received = "received"
    closed = "closed"


# --------------------------------------------------------------------------- #
# 5.1 Cut order
# --------------------------------------------------------------------------- #
class CutOrder(TimestampMixin, table=True):
    __tablename__ = "cut_order"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_number: str = Field(index=True, unique=True)
    sales_order_id: Optional[int] = Field(default=None, foreign_key="sales_order.id")
    style_id: int = Field(foreign_key="style.id", index=True)
    colour_id: int = Field(foreign_key="colour.id")
    # FK to a specific approved BOM version — never the style (locked-in).
    bom_version_id: int = Field(foreign_key="bom_version.id")
    fabric_material_id: int = Field(foreign_key="material.id")
    marker_efficiency: Optional[Decimal] = rate_field(default=None, nullable=True)

    fabric_required: Decimal = quantity_field(default=Decimal("0"))
    fabric_issued: Decimal = quantity_field(default=Decimal("0"))
    pieces_cut: int = 0
    status: CutOrderStatus = Field(default=CutOrderStatus.planned, index=True)

    sizes: List["CutOrderSize"] = Relationship(
        back_populates="cut_order",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "CutOrderSize.position",
        },
    )


class CutOrderSize(SQLModel, table=True):
    __tablename__ = "cut_order_size"

    id: Optional[int] = Field(default=None, primary_key=True)
    cut_order_id: int = Field(foreign_key="cut_order.id", index=True)
    size_label: str
    position: int
    planned_qty: int = 0
    cut_qty: int = 0

    cut_order: Optional[CutOrder] = Relationship(back_populates="sizes")


class CutIssue(SQLModel, table=True):
    """Record of a roll issued to a cut order (audit of consumption)."""

    __tablename__ = "cut_issue"

    id: Optional[int] = Field(default=None, primary_key=True)
    cut_order_id: int = Field(foreign_key="cut_order.id", index=True)
    roll_id: int = Field(foreign_key="roll.id", index=True)
    quantity: Decimal = quantity_field(default=Decimal("0"))
    ledger_entry_id: Optional[int] = Field(default=None)
    issued_at: str = Field(default_factory=lambda: utcnow().isoformat())


# --------------------------------------------------------------------------- #
# 5.4 Sewing order + 5.5 daily output
# --------------------------------------------------------------------------- #
class SewingOrder(TimestampMixin, table=True):
    __tablename__ = "sewing_order"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_number: str = Field(index=True, unique=True)
    cut_order_id: Optional[int] = Field(default=None, foreign_key="cut_order.id")
    style_id: int = Field(foreign_key="style.id", index=True)
    line: Optional[str] = None
    planned_qty: int = 0
    produced_qty: int = 0
    sam: Optional[Decimal] = rate_field(default=None, nullable=True)
    status: SewingOrderStatus = Field(default=SewingOrderStatus.planned, index=True)

    daily_outputs: List["SewingDailyOutput"] = Relationship(
        back_populates="sewing_order",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class SewingDailyOutput(SQLModel, table=True):
    __tablename__ = "sewing_daily_output"

    id: Optional[int] = Field(default=None, primary_key=True)
    sewing_order_id: int = Field(foreign_key="sewing_order.id", index=True)
    output_date: date
    shift: Optional[str] = None
    produced_qty: int = 0
    operators: int = 0
    working_minutes: int = 0
    # SAM-based line efficiency % = produced × SAM / (operators × minutes) × 100.
    efficiency_pct: Decimal = rate_field(default=Decimal("0"))
    client_key: Optional[str] = Field(default=None, index=True)

    sewing_order: Optional[SewingOrder] = Relationship(back_populates="daily_outputs")


# --------------------------------------------------------------------------- #
# 5.6 Subcontracting
# --------------------------------------------------------------------------- #
class SubcontractOrder(TimestampMixin, table=True):
    __tablename__ = "subcontract_order"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_number: str = Field(index=True, unique=True)
    subcontractor_id: int = Field(foreign_key="supplier.id", index=True)
    process: str
    cut_order_id: Optional[int] = Field(default=None, foreign_key="cut_order.id")
    sent_qty: int = 0
    received_qty: int = 0
    rate: Decimal = money_field(default=Decimal("0"))
    sent_date: Optional[date] = None
    expected_date: Optional[date] = None
    status: SubcontractStatus = Field(default=SubcontractStatus.open, index=True)


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class CutSizeInput(SQLModel):
    size_label: str
    planned_qty: int


class CutOrderCreate(SQLModel):
    sales_order_id: Optional[int] = None
    style_id: int
    colour_id: int
    bom_version_id: Optional[int] = None  # defaults to the style's current version
    marker_efficiency: Optional[Decimal] = None
    sizes: List[CutSizeInput] = []


class CutSizeRead(SQLModel):
    size_label: str
    position: int
    planned_qty: int
    cut_qty: int


class CutOrderRead(SQLModel):
    id: int
    order_number: str
    sales_order_id: Optional[int]
    style_id: int
    colour_id: int
    bom_version_id: int
    fabric_material_id: int
    fabric_required: Decimal
    fabric_issued: Decimal
    pieces_cut: int
    status: CutOrderStatus
    sizes: List[CutSizeRead]


class ReserveFabricRequest(SQLModel):
    shade_group: Optional[str] = None
    min_width_cm: Optional[Decimal] = None


class IssueAllocation(SQLModel):
    roll_id: int
    quantity: Decimal


class IssueFabricRequest(SQLModel):
    # If omitted, the cut order issues automatically against its reservations.
    allocations: List[IssueAllocation] = []


class CompleteCutRequest(SQLModel):
    cut_qty: List[CutSizeInput] = []  # actual pieces cut per size


class SewingOrderCreate(SQLModel):
    cut_order_id: Optional[int] = None
    style_id: int
    line: Optional[str] = None
    planned_qty: int = 0


class SewingOrderRead(SQLModel):
    id: int
    order_number: str
    cut_order_id: Optional[int]
    style_id: int
    line: Optional[str]
    planned_qty: int
    produced_qty: int
    sam: Optional[Decimal]
    status: SewingOrderStatus
    average_efficiency_pct: Decimal


class DailyOutputCreate(SQLModel):
    output_date: date
    shift: Optional[str] = None
    produced_qty: int
    operators: int
    working_minutes: int
    client_key: Optional[str] = None


class DailyOutputRead(SQLModel):
    id: int
    output_date: date
    shift: Optional[str]
    produced_qty: int
    operators: int
    working_minutes: int
    efficiency_pct: Decimal


class SubcontractCreate(SQLModel):
    subcontractor_id: int
    process: str
    cut_order_id: Optional[int] = None
    sent_qty: int
    rate: Decimal = Decimal("0")
    sent_date: Optional[date] = None
    expected_date: Optional[date] = None


class SubcontractReceiveRequest(SQLModel):
    received_qty: int


class SubcontractRead(SQLModel):
    id: int
    order_number: str
    subcontractor_id: int
    process: str
    cut_order_id: Optional[int]
    sent_qty: int
    received_qty: int
    outstanding_qty: int
    rate: Decimal
    status: SubcontractStatus
