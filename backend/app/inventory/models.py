"""Module 4 — Inventory & warehouse: the immutable stock ledger and roll register.

Two core structures:

* **StockLedgerEntry** — append-only movement lines. On-hand balance is *derived*
  as the sum of quantities (blueprint A3 / 4.1). Nothing is ever updated or
  deleted; a correction is a new ADJUSTMENT line. Every line carries full stock
  identity (material, roll, lot, warehouse) and a reference back to the document
  that caused it, so the ledger is a complete audit of stock movement.

* **Roll** — one physical roll of fabric as a distinct, uniquely-identified unit
  (4.2/4.3) carrying dye lot, shade code/group, length, weight, width, GSM,
  grade, location and status. The roll register is queryable by shade group,
  width, grade and status to drive roll selection (4.6).
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel

from ..kernel.audit import TimestampMixin, utcnow
from ..kernel.types import quantity_field


class MovementType(str, Enum):
    receipt = "receipt"          # goods received into stock (+)
    issue = "issue"              # issued to production / dispatch (-)
    return_to_stock = "return"   # unused material returned (+)
    adjustment = "adjustment"    # manual correction / opening balance (±)
    transfer_in = "transfer_in"  # inter-location move (+)
    transfer_out = "transfer_out"  # inter-location move (-)


class RollStatus(str, Enum):
    # Lifecycle: a roll is created pending inspection, four-point inspection
    # (7.1) moves it to available or quarantined, reservations mark it reserved,
    # production consumes it.
    pending_inspection = "pending_inspection"
    available = "available"
    reserved = "reserved"
    quarantined = "quarantined"
    consumed = "consumed"
    rejected = "rejected"


class Grade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


# --------------------------------------------------------------------------- #
# 4.1 Stock ledger (immutable, append-only)
# --------------------------------------------------------------------------- #
class StockLedgerEntry(SQLModel, table=True):
    __tablename__ = "stock_ledger_entry"

    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    roll_id: Optional[int] = Field(default=None, foreign_key="roll.id", index=True)

    movement_type: MovementType
    # Signed base-UoM quantity: positive into stock, negative out. Balance is the
    # sum of these — never stored, always derived.
    quantity: Decimal = quantity_field(default=Decimal("0"))
    uom: str = "metre"

    warehouse: str = "MAIN"
    location: Optional[str] = None

    # Stock identity for bulk (non-roll) lots; roll lines carry identity on Roll.
    lot: Optional[str] = None

    # Traceability: which document caused this movement.
    reference_type: Optional[str] = Field(default=None, index=True)
    reference_id: Optional[int] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    created_by: Optional[str] = None
    note: Optional[str] = None


class StockLedgerEntryRead(SQLModel):
    id: int
    material_id: int
    roll_id: Optional[int]
    movement_type: MovementType
    quantity: Decimal
    uom: str
    warehouse: str
    location: Optional[str]
    lot: Optional[str]
    reference_type: Optional[str]
    reference_id: Optional[int]
    created_at: datetime
    note: Optional[str]


# --------------------------------------------------------------------------- #
# 4.2 / 4.3 Roll register
# --------------------------------------------------------------------------- #
class RollBase(SQLModel):
    material_id: int = Field(foreign_key="material.id", index=True)
    dye_lot: Optional[str] = Field(default=None, index=True)
    shade_code: Optional[str] = None
    shade_group: Optional[str] = Field(default=None, index=True)
    length: Decimal = quantity_field(default=Decimal("0"))  # original length, base UoM
    weight: Optional[Decimal] = quantity_field(default=None, nullable=True)
    width_cm: Optional[Decimal] = quantity_field(default=None, nullable=True)
    gsm: Optional[Decimal] = quantity_field(default=None, nullable=True)
    grade: Grade = Field(default=Grade.A, index=True)
    warehouse: str = "MAIN"
    location: Optional[str] = None


class Roll(RollBase, TimestampMixin, table=True):
    __tablename__ = "roll"

    id: Optional[int] = Field(default=None, primary_key=True)
    roll_number: str = Field(index=True, unique=True)
    status: RollStatus = Field(default=RollStatus.pending_inspection, index=True)
    supplier_id: Optional[int] = Field(default=None, foreign_key="supplier.id")
    goods_receipt_id: Optional[int] = Field(default=None, index=True)
    # 4.6 customer restriction: a roll dedicated to one customer is only
    # selectable for that customer's orders.
    restricted_customer_id: Optional[int] = Field(
        default=None, foreign_key="customer.id"
    )


class RollRead(RollBase):
    id: int
    roll_number: str
    status: RollStatus
    supplier_id: Optional[int]
    goods_receipt_id: Optional[int]


class RollWithBalance(RollRead):
    on_hand: Decimal


class StockAdjustmentCreate(SQLModel):
    """Manual stock adjustment / opening balance for bulk (non-roll) materials."""

    material_id: int
    quantity: Decimal
    uom: str = "metre"
    warehouse: str = "MAIN"
    location: Optional[str] = None
    lot: Optional[str] = None
    note: Optional[str] = None


# --------------------------------------------------------------------------- #
# 4.4 Reservations (lifecycle Active → Released → Consumed)
# --------------------------------------------------------------------------- #
class ReservationStatus(str, Enum):
    active = "active"
    released = "released"
    consumed = "consumed"


class Reservation(TimestampMixin, table=True):
    __tablename__ = "reservation"

    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    roll_id: Optional[int] = Field(default=None, foreign_key="roll.id", index=True)
    reserved_qty: Decimal = quantity_field(default=Decimal("0"))
    status: ReservationStatus = Field(default=ReservationStatus.active, index=True)
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    # What the reservation is for, e.g. ("cut_order", 42).
    reference_type: Optional[str] = Field(default=None, index=True)
    reference_id: Optional[int] = Field(default=None, index=True)


class ReservationRead(SQLModel):
    id: int
    material_id: int
    roll_id: Optional[int]
    reserved_qty: Decimal
    status: ReservationStatus
    customer_id: Optional[int]
    reference_type: Optional[str]
    reference_id: Optional[int]


class RollAllocation(SQLModel):
    """One line of a roll-selection result: take ``qty`` from ``roll_id``."""

    roll_id: int
    roll_number: str
    qty: Decimal


class ReserveRequest(SQLModel):
    material_id: int
    required_qty: Decimal
    shade_group: Optional[str] = None
    min_width_cm: Optional[Decimal] = None
    grade: Optional[Grade] = None
    customer_id: Optional[int] = None
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
