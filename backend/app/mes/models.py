"""Module 12 — MES: shift execution logs, downtime, and OEE.

OEE is the standard three-factor product:

    Availability = run time / planned production time
    Performance  = (ideal cycle time × total count) / run time
    Quality      = good count / total count
    OEE          = Availability × Performance × Quality

The subtlety that makes or breaks an OEE number is *planned production time*.
Downtime that was scheduled — a planned changeover, booked maintenance, a shift
the line was never meant to run — is excluded from the denominator rather than
counted as a loss. Otherwise every factory that maintains its machines scores
worse than one that runs them to failure, which is exactly backwards.

So each :class:`DowntimeReason` carries a ``planned`` flag:

    planned_production_time = planned_minutes − planned downtime
    run_time                = planned_production_time − unplanned downtime

A shift log is *open* while the floor is still recording against it and *closed*
once the numbers are frozen. OEE is computed and stored at close, for the same
reason cost and MRP runs are frozen: someone acts on the number, and it has to
still mean what it meant when they acted.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin, utcnow
from ..kernel.types import quantity_field, rate_field


class DowntimeCategory(str, Enum):
    breakdown = "breakdown"
    changeover = "changeover"
    material_shortage = "material_shortage"
    no_operator = "no_operator"
    quality_issue = "quality_issue"
    power = "power"
    planned_maintenance = "planned_maintenance"
    other = "other"


class ShiftLogStatus(str, Enum):
    open = "open"
    closed = "closed"


class Machine(TimestampMixin, table=True):
    """A physical asset inside a work centre.

    ``ideal_cycle_seconds`` is the rated time to produce one piece at full
    speed. It is the denominator-free half of the performance factor and the
    single most commonly wrong number in an OEE implementation — if it is set
    optimistically, performance quietly reads above 100%.
    """

    __tablename__ = "machine"
    __table_args__ = (UniqueConstraint("code", name="uq_machine_code"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, max_length=32)
    name: str = Field(max_length=120)
    work_centre_id: int = Field(foreign_key="work_centre.id", index=True)
    ideal_cycle_seconds: Decimal = rate_field(default=Decimal("0"))
    active: bool = Field(default=True, index=True)


class DowntimeReason(TimestampMixin, table=True):
    """A coded stoppage cause.

    ``planned`` decides whether the stoppage is removed from the OEE
    denominator or counted as an availability loss.
    """

    __tablename__ = "downtime_reason"
    __table_args__ = (UniqueConstraint("code", name="uq_downtime_reason_code"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, max_length=32)
    description: str = Field(max_length=200)
    category: DowntimeCategory = Field(index=True)
    planned: bool = Field(default=False, index=True)
    active: bool = Field(default=True, index=True)


class ShiftLog(TimestampMixin, table=True):
    """One work centre (optionally one machine) for one shift."""

    __tablename__ = "shift_log"
    __table_args__ = (
        UniqueConstraint("log_number", name="uq_shift_log_number"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    log_number: str = Field(index=True)
    work_centre_id: int = Field(foreign_key="work_centre.id", index=True)
    machine_id: Optional[int] = Field(default=None, foreign_key="machine.id", index=True)
    sewing_order_id: Optional[int] = Field(
        default=None, foreign_key="sewing_order.id", index=True
    )
    log_date: date = Field(index=True)
    shift: str = Field(default="A", max_length=16, index=True)
    operators: int = Field(default=0)

    planned_minutes: Decimal = quantity_field(default=Decimal("0"))
    total_count: int = Field(default=0)
    good_count: int = Field(default=0)
    reject_count: int = Field(default=0)
    # Overrides the machine's rated cycle when the run is a known-slower style.
    ideal_cycle_seconds: Optional[Decimal] = rate_field(default=None, nullable=True)

    status: ShiftLogStatus = Field(default=ShiftLogStatus.open, index=True)

    # Frozen at close.
    planned_downtime_minutes: Decimal = quantity_field(default=Decimal("0"))
    unplanned_downtime_minutes: Decimal = quantity_field(default=Decimal("0"))
    run_minutes: Decimal = quantity_field(default=Decimal("0"))
    availability_pct: Decimal = rate_field(default=Decimal("0"))
    performance_pct: Decimal = rate_field(default=Decimal("0"))
    quality_pct: Decimal = rate_field(default=Decimal("0"))
    oee_pct: Decimal = rate_field(default=Decimal("0"))
    performance_capped: bool = Field(default=False)

    closed_at: Optional[str] = None
    closed_by: Optional[str] = None
    notes: Optional[str] = None

    downtime: List["DowntimeEvent"] = Relationship(
        back_populates="shift_log",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class DowntimeEvent(SQLModel, table=True):
    __tablename__ = "downtime_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    shift_log_id: int = Field(foreign_key="shift_log.id", index=True)
    reason_id: int = Field(foreign_key="downtime_reason.id", index=True)
    minutes: Decimal = quantity_field(default=Decimal("0"))
    note: Optional[str] = Field(default=None, max_length=300)
    recorded_at: datetime = Field(default_factory=utcnow, nullable=False)
    recorded_by: Optional[str] = None

    shift_log: Optional[ShiftLog] = Relationship(back_populates="downtime")


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class MachineCreate(SQLModel):
    code: str
    name: str
    work_centre_id: int
    ideal_cycle_seconds: Decimal = Decimal("0")


class MachineRead(SQLModel):
    id: int
    code: str
    name: str
    work_centre_id: int
    ideal_cycle_seconds: Decimal
    active: bool


class DowntimeReasonCreate(SQLModel):
    code: str
    description: str
    category: DowntimeCategory
    planned: bool = False


class DowntimeReasonRead(SQLModel):
    id: int
    code: str
    description: str
    category: DowntimeCategory
    planned: bool
    active: bool


class ShiftLogCreate(SQLModel):
    work_centre_id: int
    log_date: date
    shift: str = "A"
    machine_id: Optional[int] = None
    sewing_order_id: Optional[int] = None
    operators: int = 0
    planned_minutes: Optional[Decimal] = None
    ideal_cycle_seconds: Optional[Decimal] = None
    notes: Optional[str] = None


class ShiftCountUpdate(SQLModel):
    """Cumulative counts as read off the line, not deltas."""

    total_count: Optional[int] = None
    good_count: Optional[int] = None
    reject_count: Optional[int] = None


class DowntimeEventCreate(SQLModel):
    reason_id: int
    minutes: Decimal
    note: Optional[str] = None


class DowntimeEventRead(SQLModel):
    id: int
    reason_id: int
    reason_code: Optional[str] = None
    reason_description: Optional[str] = None
    category: Optional[DowntimeCategory] = None
    planned: bool = False
    minutes: Decimal
    note: Optional[str]
    recorded_by: Optional[str]


class ShiftLogRead(SQLModel):
    id: int
    log_number: str
    work_centre_id: int
    work_centre_code: Optional[str] = None
    machine_id: Optional[int]
    machine_code: Optional[str] = None
    sewing_order_id: Optional[int]
    log_date: date
    shift: str
    operators: int
    planned_minutes: Decimal
    total_count: int
    good_count: int
    reject_count: int
    ideal_cycle_seconds: Optional[Decimal]
    status: ShiftLogStatus
    planned_downtime_minutes: Decimal
    unplanned_downtime_minutes: Decimal
    run_minutes: Decimal
    availability_pct: Decimal
    performance_pct: Decimal
    quality_pct: Decimal
    oee_pct: Decimal
    performance_capped: bool
    closed_at: Optional[str]
    closed_by: Optional[str]
    notes: Optional[str]
    downtime: List[DowntimeEventRead] = []


class DowntimeParetoLine(SQLModel):
    reason_id: int
    reason_code: str
    description: str
    category: DowntimeCategory
    planned: bool
    minutes: Decimal
    events: int
    share_pct: Decimal
    cumulative_pct: Decimal


class OeeSummaryRead(SQLModel):
    """Aggregate OEE over a date range, optionally for one work centre."""

    date_from: date
    date_to: date
    work_centre_id: Optional[int]
    shifts: int
    planned_minutes: Decimal
    planned_downtime_minutes: Decimal
    unplanned_downtime_minutes: Decimal
    run_minutes: Decimal
    total_count: int
    good_count: int
    reject_count: int
    availability_pct: Decimal
    performance_pct: Decimal
    quality_pct: Decimal
    oee_pct: Decimal
    pareto: List[DowntimeParetoLine] = []


class AndonLine(SQLModel):
    work_centre_id: int
    code: str
    name: str
    open_logs: int
    today_oee_pct: Decimal
    today_downtime_minutes: Decimal
    status: str  # running | stopped | idle
    top_downtime_reason: Optional[str] = None


class AndonBoardRead(SQLModel):
    as_of: date
    lines: List[AndonLine]
