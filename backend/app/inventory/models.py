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
from typing import List, Optional

from sqlmodel import Field, SQLModel

from ..kernel.audit import TimestampMixin, utcnow
from ..kernel.types import money_field, quantity_field


class MovementType(str, Enum):
    receipt = "receipt"          # goods received into stock (+)
    issue = "issue"              # issued to production / dispatch (-)
    return_to_stock = "return"   # unused material returned (+)
    adjustment = "adjustment"    # manual correction / opening balance (±)
    transfer_in = "transfer_in"  # inter-location move (+)
    transfer_out = "transfer_out"  # inter-location move (-)
    supplier_return = "supplier_return"  # returned to supplier (-)
    customer_return = "customer_return"  # returned by customer (+)
    split_out = "split_out"        # source roll consumed by split (-)
    split_in = "split_in"          # child roll created by split (+)
    join_out = "join_out"          # source roll consumed by join (-)
    join_in = "join_in"            # joined roll created (+)
    cycle_count = "cycle_count"    # counted-vs-system variance (+/-)


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
    unit_cost: Decimal = money_field(default=Decimal("0"))
    extended_cost: Decimal = money_field(default=Decimal("0"))
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
    unit_cost: Decimal
    extended_cost: Decimal
    uom: str
    warehouse: str
    location: Optional[str]
    lot: Optional[str]
    reference_type: Optional[str]
    reference_id: Optional[int]
    created_at: datetime
    note: Optional[str]


class InventoryCostLayer(SQLModel, table=True):
    """Open receipt layer used when a material is valued FIFO."""

    __tablename__ = "inventory_cost_layer"

    id: Optional[int] = Field(default=None, primary_key=True)
    receipt_ledger_entry_id: int = Field(foreign_key="stock_ledger_entry.id", index=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    roll_id: Optional[int] = Field(default=None, foreign_key="roll.id", index=True)
    warehouse: str = "MAIN"
    location: Optional[str] = None
    received_qty: Decimal = quantity_field(default=Decimal("0"))
    remaining_qty: Decimal = quantity_field(default=Decimal("0"))
    unit_cost: Decimal = money_field(default=Decimal("0"))
    created_at: datetime = Field(default_factory=utcnow, nullable=False)


class StockValuationRead(SQLModel):
    material_id: int
    warehouse: Optional[str]
    location: Optional[str]
    quantity: Decimal
    value: Decimal
    unit_cost: Decimal


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
    parent_roll_id: Optional[int] = Field(default=None, foreign_key="roll.id", index=True)
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
    parent_roll_id: Optional[int] = None


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


# --------------------------------------------------------------------------- #
# Warehouse operations - every physical action has a durable header and lines.
# --------------------------------------------------------------------------- #
class WarehouseOperationType(str, Enum):
    transfer = "transfer"
    return_to_supplier = "return_to_supplier"
    customer_return = "customer_return"
    return_to_stock = "return_to_stock"
    put_away = "put_away"
    split_roll = "split_roll"
    join_rolls = "join_rolls"
    regrade = "regrade"
    cycle_count = "cycle_count"


class WarehouseOperation(SQLModel, table=True):
    __tablename__ = "warehouse_operation"

    id: Optional[int] = Field(default=None, primary_key=True)
    operation_number: str = Field(index=True, unique=True)
    operation_type: WarehouseOperationType = Field(index=True)
    reference: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    created_by: Optional[str] = None


class WarehouseOperationLine(SQLModel, table=True):
    __tablename__ = "warehouse_operation_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    operation_id: int = Field(foreign_key="warehouse_operation.id", index=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    roll_id: Optional[int] = Field(default=None, foreign_key="roll.id", index=True)
    result_roll_id: Optional[int] = Field(default=None, foreign_key="roll.id")
    quantity: Decimal = quantity_field(default=Decimal("0"))
    from_warehouse: Optional[str] = None
    from_location: Optional[str] = None
    to_warehouse: Optional[str] = None
    to_location: Optional[str] = None
    old_grade: Optional[Grade] = None
    new_grade: Optional[Grade] = None
    system_qty: Optional[Decimal] = quantity_field(default=None, nullable=True)
    counted_qty: Optional[Decimal] = quantity_field(default=None, nullable=True)
    variance_qty: Optional[Decimal] = quantity_field(default=None, nullable=True)


class WarehouseOperationLineRead(SQLModel):
    material_id: int
    roll_id: Optional[int]
    result_roll_id: Optional[int]
    quantity: Decimal
    from_warehouse: Optional[str]
    from_location: Optional[str]
    to_warehouse: Optional[str]
    to_location: Optional[str]
    old_grade: Optional[Grade]
    new_grade: Optional[Grade]
    system_qty: Optional[Decimal]
    counted_qty: Optional[Decimal]
    variance_qty: Optional[Decimal]


class WarehouseOperationRead(SQLModel):
    id: int
    operation_number: str
    operation_type: WarehouseOperationType
    reference: Optional[str]
    reason: Optional[str]
    created_at: datetime
    created_by: Optional[str]
    lines: List[WarehouseOperationLineRead] = []


class MoveStockRequest(SQLModel):
    material_id: int
    quantity: Decimal
    roll_id: Optional[int] = None
    from_warehouse: str = "MAIN"
    from_location: Optional[str] = None
    to_warehouse: str = "MAIN"
    to_location: Optional[str] = None
    reference: Optional[str] = None
    reason: Optional[str] = None


class ReturnStockRequest(SQLModel):
    material_id: int
    quantity: Decimal
    direction: str = "supplier"
    roll_id: Optional[int] = None
    warehouse: str = "MAIN"
    location: Optional[str] = None
    reference: Optional[str] = None
    reason: str


class SplitPartInput(SQLModel):
    quantity: Decimal
    roll_number: Optional[str] = None
    location: Optional[str] = None


class SplitRollRequest(SQLModel):
    parts: List[SplitPartInput]
    reason: Optional[str] = None


class JoinRollsRequest(SQLModel):
    roll_ids: List[int]
    roll_number: Optional[str] = None
    reason: Optional[str] = None


class RegradeRollRequest(SQLModel):
    grade: Grade
    reason: str


class CycleCountLineInput(SQLModel):
    material_id: int
    counted_qty: Decimal
    roll_id: Optional[int] = None
    warehouse: str = "MAIN"
    location: Optional[str] = None


class CycleCountRequest(SQLModel):
    reference: Optional[str] = None
    reason: Optional[str] = None
    lines: List[CycleCountLineInput]
