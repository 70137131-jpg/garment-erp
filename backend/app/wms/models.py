"""Module 13 — mobile WMS: bins, directed work, and scanning.

The inventory module already owns stock *movement*. This module owns the
**work**: who is meant to move what, where to, in what order, and whether it
has been done. Nothing here writes stock directly — completing a task calls
``inventory.service.move_stock``, so the ledger stays the only place a balance
can change.

Two decisions shaped by the handheld:

* **Bins are a master, not a free-text location.** ``StockLedgerEntry.location``
  stays a string for compatibility, but a bin has a code, a zone, a type and a
  pick sequence. Without that, a pick list cannot be walked in a sensible order
  and a scanned location cannot be validated.
* **Every completion is idempotent.** A handheld on a bad Wi-Fi link retries.
  Tasks therefore carry a ``client_key``: replaying a completion returns the
  original result instead of moving the stock twice. This reuses the same
  pattern as goods receipt and daily output.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from ..kernel.audit import TimestampMixin, utcnow
from ..kernel.types import quantity_field


class BinType(str, Enum):
    storage = "storage"
    staging = "staging"
    receiving = "receiving"
    shipping = "shipping"
    quarantine = "quarantine"


class Bin(TimestampMixin, table=True):
    """A physical location a handheld can scan.

    ``pick_sequence`` orders bins along the walking route, so a multi-line pick
    list can be sorted into the order a person actually encounters them.
    """

    __tablename__ = "bin"
    __table_args__ = (
        UniqueConstraint("warehouse", "code", name="uq_bin_warehouse_code"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, max_length=32)
    warehouse: str = Field(default="MAIN", index=True, max_length=32)
    zone: Optional[str] = Field(default=None, max_length=32, index=True)
    bin_type: BinType = Field(default=BinType.storage, index=True)
    pick_sequence: int = Field(default=0, index=True)
    capacity_qty: Optional[Decimal] = quantity_field(default=None, nullable=True)
    active: bool = Field(default=True, index=True)


class TaskType(str, Enum):
    put_away = "put_away"
    pick = "pick"
    replenish = "replenish"
    count = "count"
    transfer = "transfer"


class TaskStatus(str, Enum):
    open = "open"
    assigned = "assigned"
    completed = "completed"
    cancelled = "cancelled"


class WarehouseTask(TimestampMixin, table=True):
    """One directed movement for one operator."""

    __tablename__ = "warehouse_task"
    __table_args__ = (
        UniqueConstraint("task_number", name="uq_warehouse_task_number"),
        # The idempotency boundary for handheld retries.
        UniqueConstraint("client_key", name="uq_warehouse_task_client_key"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    task_number: str = Field(index=True)
    task_type: TaskType = Field(index=True)
    status: TaskStatus = Field(default=TaskStatus.open, index=True)
    priority: int = Field(default=5, index=True)

    material_id: int = Field(foreign_key="material.id", index=True)
    roll_id: Optional[int] = Field(default=None, foreign_key="roll.id", index=True)
    quantity: Decimal = quantity_field(default=Decimal("0"))

    from_bin_id: Optional[int] = Field(default=None, foreign_key="bin.id", index=True)
    to_bin_id: Optional[int] = Field(default=None, foreign_key="bin.id", index=True)

    reference_type: Optional[str] = Field(default=None, index=True)
    reference_id: Optional[int] = Field(default=None, index=True)

    assigned_to: Optional[str] = Field(default=None, index=True, max_length=320)
    due_date: Optional[date] = Field(default=None, index=True)

    completed_qty: Decimal = quantity_field(default=Decimal("0"))
    completed_at: Optional[datetime] = None
    completed_by: Optional[str] = None
    # Set when the operator completes a different quantity than directed.
    short_reason: Optional[str] = Field(default=None, max_length=200)
    client_key: Optional[str] = Field(default=None, index=True, max_length=100)
    warehouse_operation_id: Optional[int] = Field(default=None, index=True)
    notes: Optional[str] = Field(default=None, max_length=300)


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class BinCreate(SQLModel):
    code: str
    warehouse: str = "MAIN"
    zone: Optional[str] = None
    bin_type: BinType = BinType.storage
    pick_sequence: int = 0
    capacity_qty: Optional[Decimal] = None


class BinRead(SQLModel):
    id: int
    code: str
    warehouse: str
    zone: Optional[str]
    bin_type: BinType
    pick_sequence: int
    capacity_qty: Optional[Decimal]
    active: bool


class TaskCreate(SQLModel):
    task_type: TaskType
    material_id: int
    quantity: Decimal
    roll_id: Optional[int] = None
    from_bin_id: Optional[int] = None
    to_bin_id: Optional[int] = None
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    assigned_to: Optional[str] = None
    priority: int = 5
    due_date: Optional[date] = None
    notes: Optional[str] = None


class TaskAssign(SQLModel):
    assigned_to: str


class TaskComplete(SQLModel):
    """What the operator actually did, which may not be what was directed."""

    completed_qty: Optional[Decimal] = None
    to_bin_id: Optional[int] = None
    short_reason: Optional[str] = None
    client_key: Optional[str] = None


class TaskRead(SQLModel):
    id: int
    task_number: str
    task_type: TaskType
    status: TaskStatus
    priority: int
    material_id: int
    material_code: Optional[str] = None
    material_name: Optional[str] = None
    roll_id: Optional[int]
    roll_number: Optional[str] = None
    quantity: Decimal
    from_bin_id: Optional[int]
    from_bin_code: Optional[str] = None
    to_bin_id: Optional[int]
    to_bin_code: Optional[str] = None
    reference_type: Optional[str]
    reference_id: Optional[int]
    assigned_to: Optional[str]
    due_date: Optional[date]
    completed_qty: Decimal
    completed_at: Optional[datetime]
    completed_by: Optional[str]
    short_reason: Optional[str]
    warehouse_operation_id: Optional[int]
    notes: Optional[str]


class ScanRequest(SQLModel):
    code: str


class ScanResult(SQLModel):
    """Compact answer for a handheld: what did I just scan?"""

    kind: str  # roll | bin | material | unknown
    id: Optional[int] = None
    code: Optional[str] = None
    label: Optional[str] = None
    material_id: Optional[int] = None
    material_code: Optional[str] = None
    material_name: Optional[str] = None
    quantity: Optional[Decimal] = None
    uom: Optional[str] = None
    warehouse: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None
    grade: Optional[str] = None
    open_tasks: List[TaskRead] = []
