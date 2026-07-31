"""Module 11 — Planning: MRP, ATP, and finite capacity.

Three related jobs share this module because they share one question: *can we
actually deliver what we have promised?*

* **MRP** — time-phased material netting. Gross requirements from confirmed
  orders, netted against on-hand and scheduled receipts bucket by bucket, then
  offset by lead time into planned order releases.
* **Capacity** — work centres with a daily minute capacity, bookings that
  consume it, and a board that shows load against capacity per bucket.
* **ATP** — available-to-promise: given a style, a quantity and a wanted date,
  what can honestly be committed, and what is the binding constraint.

A note on why MRP runs are *stored* rather than computed on demand: a planner
acts on a run (raises requisitions, promises a date). If the numbers silently
moved underneath them the next time stock changed, the decision could never be
explained afterwards. A run is therefore a frozen document, like a cost sheet
version or an actual-cost run.
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import quantity_field, rate_field


# --------------------------------------------------------------------------- #
# 11.1 Work centres and capacity
# --------------------------------------------------------------------------- #
class WorkCentreType(str, Enum):
    cutting = "cutting"
    sewing = "sewing"
    finishing = "finishing"
    packing = "packing"
    embroidery = "embroidery"
    washing = "washing"


class WorkCentre(TimestampMixin, table=True):
    """A finite resource that consumes production minutes.

    Nominal daily capacity is ``operators × shift_minutes × efficiency``. The
    efficiency factor is what stops the board from promising a theoretical
    maximum no line has ever achieved.
    """

    __tablename__ = "work_centre"
    __table_args__ = (UniqueConstraint("code", name="uq_work_centre_code"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, max_length=32)
    name: str = Field(max_length=120)
    centre_type: WorkCentreType = Field(index=True)
    operators: int = Field(default=0)
    shift_minutes: int = Field(default=480)
    shifts_per_day: int = Field(default=1)
    efficiency_pct: Decimal = rate_field(default=Decimal("100"))
    active: bool = Field(default=True, index=True)

    @property
    def daily_minutes(self) -> Decimal:
        """Nominal capacity for one working day."""
        gross = Decimal(self.operators) * Decimal(self.shift_minutes) * Decimal(
            self.shifts_per_day
        )
        return gross * (self.efficiency_pct or Decimal("0")) / Decimal("100")


class CapacityException(SQLModel, table=True):
    """A day where a work centre's capacity is not the nominal figure.

    Covers holidays (0 minutes), overtime, and unplanned downtime. One row per
    centre per day, so the override is unambiguous.
    """

    __tablename__ = "capacity_exception"
    __table_args__ = (
        UniqueConstraint(
            "work_centre_id", "exception_date", name="uq_capacity_exception_day"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    work_centre_id: int = Field(foreign_key="work_centre.id", index=True)
    exception_date: date = Field(index=True)
    available_minutes: Decimal = quantity_field(default=Decimal("0"))
    reason: Optional[str] = Field(default=None, max_length=200)


class CapacityBookingStatus(str, Enum):
    planned = "planned"
    confirmed = "confirmed"
    released = "released"
    cancelled = "cancelled"


class CapacityBooking(TimestampMixin, table=True):
    """Minutes reserved on a work centre for a production order.

    Booked minutes are spread evenly across the working days in the range; the
    board resolves the daily share rather than storing it, so moving a booking
    never leaves orphaned day rows behind.
    """

    __tablename__ = "capacity_booking"

    id: Optional[int] = Field(default=None, primary_key=True)
    work_centre_id: int = Field(foreign_key="work_centre.id", index=True)
    sewing_order_id: Optional[int] = Field(
        default=None, foreign_key="sewing_order.id", index=True
    )
    cut_order_id: Optional[int] = Field(
        default=None, foreign_key="cut_order.id", index=True
    )
    sales_order_id: Optional[int] = Field(
        default=None, foreign_key="sales_order.id", index=True
    )
    description: Optional[str] = Field(default=None, max_length=200)
    start_date: date = Field(index=True)
    end_date: date = Field(index=True)
    minutes: Decimal = quantity_field(default=Decimal("0"))
    status: CapacityBookingStatus = Field(
        default=CapacityBookingStatus.planned, index=True
    )


# --------------------------------------------------------------------------- #
# 11.2 MRP runs
# --------------------------------------------------------------------------- #
class MrpRunStatus(str, Enum):
    draft = "draft"
    firmed = "firmed"


class MrpRun(TimestampMixin, table=True):
    """A frozen time-phased netting of every material over a horizon."""

    __tablename__ = "mrp_run"
    __table_args__ = (UniqueConstraint("run_number", name="uq_mrp_run_number"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    run_number: str = Field(index=True)
    horizon_start: date
    horizon_end: date
    bucket_days: int = Field(default=7)
    status: MrpRunStatus = Field(default=MrpRunStatus.draft, index=True)
    generated_by: Optional[str] = None
    notes: Optional[str] = None

    buckets: List["MrpBucket"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    planned_orders: List["PlannedOrder"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class MrpBucket(SQLModel, table=True):
    """One material in one time bucket — the row a planner actually reads.

    ``projected_available`` is the closing balance *after* any planned order in
    this bucket lands. It is stored rather than derived so the arithmetic a
    planner saw is reproducible even after the underlying stock moves.
    """

    __tablename__ = "mrp_bucket"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="mrp_run.id", index=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    sequence: int = Field(default=0, index=True)
    bucket_start: date = Field(index=True)
    bucket_end: date

    opening_balance: Decimal = quantity_field(default=Decimal("0"))
    gross_requirement: Decimal = quantity_field(default=Decimal("0"))
    scheduled_receipts: Decimal = quantity_field(default=Decimal("0"))
    net_requirement: Decimal = quantity_field(default=Decimal("0"))
    planned_order_qty: Decimal = quantity_field(default=Decimal("0"))
    projected_available: Decimal = quantity_field(default=Decimal("0"))

    run: Optional[MrpRun] = Relationship(back_populates="buckets")


class MrpDemandSource(SQLModel, table=True):
    """Traceability from a bucket's gross requirement back to a sales order."""

    __tablename__ = "mrp_demand_source"

    id: Optional[int] = Field(default=None, primary_key=True)
    bucket_id: int = Field(foreign_key="mrp_bucket.id", index=True)
    sales_order_id: int = Field(foreign_key="sales_order.id", index=True)
    quantity: Decimal = quantity_field(default=Decimal("0"))
    need_date: Optional[date] = None


class PlannedOrderStatus(str, Enum):
    planned = "planned"
    firmed = "firmed"
    cancelled = "cancelled"


class PlannedOrder(TimestampMixin, table=True):
    """A suggested purchase, offset backwards by lead time.

    ``release_date`` is when procurement must act; ``need_date`` is when the
    material has to be on the floor. The gap between them is the lead time the
    material master declares.
    """

    __tablename__ = "planned_order"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="mrp_run.id", index=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    quantity: Decimal = quantity_field(default=Decimal("0"))
    need_date: date = Field(index=True)
    release_date: date = Field(index=True)
    lead_time_days: int = Field(default=0)
    status: PlannedOrderStatus = Field(default=PlannedOrderStatus.planned, index=True)
    requisition_id: Optional[int] = Field(
        default=None, foreign_key="purchase_requisition.id", index=True
    )
    # True when release_date is already in the past — the order is late before
    # it is even raised, which is the single most useful flag on the screen.
    past_due: bool = Field(default=False, index=True)

    run: Optional[MrpRun] = Relationship(back_populates="planned_orders")


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class WorkCentreCreate(SQLModel):
    code: str
    name: str
    centre_type: WorkCentreType
    operators: int = 0
    shift_minutes: int = 480
    shifts_per_day: int = 1
    efficiency_pct: Decimal = Decimal("100")


class WorkCentreUpdate(SQLModel):
    name: Optional[str] = None
    operators: Optional[int] = None
    shift_minutes: Optional[int] = None
    shifts_per_day: Optional[int] = None
    efficiency_pct: Optional[Decimal] = None
    active: Optional[bool] = None


class WorkCentreRead(SQLModel):
    id: int
    code: str
    name: str
    centre_type: WorkCentreType
    operators: int
    shift_minutes: int
    shifts_per_day: int
    efficiency_pct: Decimal
    daily_minutes: Decimal
    active: bool


class CapacityExceptionCreate(SQLModel):
    work_centre_id: int
    exception_date: date
    available_minutes: Decimal = Decimal("0")
    reason: Optional[str] = None


class CapacityExceptionRead(SQLModel):
    id: int
    work_centre_id: int
    exception_date: date
    available_minutes: Decimal
    reason: Optional[str]


class CapacityBookingCreate(SQLModel):
    work_centre_id: int
    start_date: date
    end_date: date
    minutes: Optional[Decimal] = None
    sewing_order_id: Optional[int] = None
    cut_order_id: Optional[int] = None
    sales_order_id: Optional[int] = None
    description: Optional[str] = None


class CapacityBookingRead(SQLModel):
    id: int
    work_centre_id: int
    sewing_order_id: Optional[int]
    cut_order_id: Optional[int]
    sales_order_id: Optional[int]
    description: Optional[str]
    start_date: date
    end_date: date
    minutes: Decimal
    status: CapacityBookingStatus


class CapacityBucketRead(SQLModel):
    bucket_start: date
    bucket_end: date
    capacity_minutes: Decimal
    loaded_minutes: Decimal
    available_minutes: Decimal
    utilisation_pct: Decimal
    overloaded: bool


class WorkCentreLoadRead(SQLModel):
    work_centre_id: int
    code: str
    name: str
    centre_type: WorkCentreType
    buckets: List[CapacityBucketRead]
    total_capacity: Decimal
    total_load: Decimal
    utilisation_pct: Decimal
    overloaded_buckets: int


class CapacityBoardRead(SQLModel):
    horizon_start: date
    horizon_end: date
    bucket_days: int
    centres: List[WorkCentreLoadRead]


class MrpRunCreate(SQLModel):
    horizon_start: Optional[date] = None
    horizon_end: Optional[date] = None
    bucket_days: int = 7
    notes: Optional[str] = None


class MrpDemandSourceRead(SQLModel):
    sales_order_id: int
    quantity: Decimal
    need_date: Optional[date]


class MrpBucketRead(SQLModel):
    id: int
    material_id: int
    material_code: Optional[str] = None
    material_name: Optional[str] = None
    sequence: int
    bucket_start: date
    bucket_end: date
    opening_balance: Decimal
    gross_requirement: Decimal
    scheduled_receipts: Decimal
    net_requirement: Decimal
    planned_order_qty: Decimal
    projected_available: Decimal
    demand_sources: List[MrpDemandSourceRead] = []


class PlannedOrderRead(SQLModel):
    id: int
    run_id: int
    material_id: int
    material_code: Optional[str] = None
    material_name: Optional[str] = None
    quantity: Decimal
    need_date: date
    release_date: date
    lead_time_days: int
    status: PlannedOrderStatus
    requisition_id: Optional[int]
    past_due: bool


class MrpRunRead(SQLModel):
    id: int
    run_number: str
    horizon_start: date
    horizon_end: date
    bucket_days: int
    status: MrpRunStatus
    generated_by: Optional[str]
    notes: Optional[str]
    material_count: int
    planned_order_count: int
    past_due_count: int


class MrpRunDetailRead(MrpRunRead):
    buckets: List[MrpBucketRead] = []
    planned_orders: List[PlannedOrderRead] = []


class FirmRequest(SQLModel):
    """Turn selected planned orders into a purchase requisition."""

    planned_order_ids: List[int] = []


class AtpRequest(SQLModel):
    style_id: int
    quantity: int
    wanted_date: date
    work_centre_id: Optional[int] = None


class AtpMaterialLine(SQLModel):
    material_id: int
    material_code: Optional[str]
    material_name: Optional[str]
    required_qty: Decimal
    available_qty: Decimal
    shortfall_qty: Decimal
    lead_time_days: int
    earliest_available: Optional[date]


class AtpResponse(SQLModel):
    style_id: int
    quantity: int
    wanted_date: date
    can_promise: bool
    promise_date: Optional[date]
    limiting_factor: str
    required_minutes: Decimal
    available_minutes: Decimal
    capacity_ready_date: Optional[date]
    material_ready_date: Optional[date]
    materials: List[AtpMaterialLine]
    notes: List[str] = []
