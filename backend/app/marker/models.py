"""Module 14 — markers and cut/lay planning.

A **marker** is the nesting of pattern pieces across the fabric width: which
sizes, how many of each, over what length. Its efficiency is the fraction of the
rectangle it actually fills:

    efficiency = pattern area / (marker length × marker width) × 100

That single number is the largest controllable lever on fabric cost in a
garment factory, and until now ``CutOrder.marker_efficiency`` was a value
someone typed in. Here it is derived from the marker's own geometry, so it can
be wrong only if the pattern area is wrong.

A **cut plan** solves the lay-planning problem: given an order's quantity per
size and a library of markers, how many plies of which marker to spread so the
order is covered using the least fabric. See :mod:`app.marker.solver` for what
the solver does and — more importantly — what it does not claim.
"""

from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import quantity_field, rate_field


class MarkerStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    retired = "retired"


class CutPlanStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    cancelled = "cancelled"


class Marker(TimestampMixin, table=True):
    """One nesting layout at a specific fabric width."""

    __tablename__ = "marker"
    __table_args__ = (UniqueConstraint("marker_code", name="uq_marker_code"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    marker_code: str = Field(index=True, max_length=40)
    style_id: int = Field(foreign_key="style.id", index=True)
    bom_version_id: Optional[int] = Field(
        default=None, foreign_key="bom_version.id", index=True
    )
    # Geometry. Width must match the fabric being spread, or the nesting is
    # simply not valid for that roll.
    width_cm: Decimal = quantity_field(default=Decimal("0"))
    length_cm: Decimal = quantity_field(default=Decimal("0"))
    # Summed area of every pattern piece in the marker. The honest input.
    pattern_area_cm2: Decimal = quantity_field(default=Decimal("0"))
    # Derived at save time from the three fields above.
    efficiency_pct: Decimal = rate_field(default=Decimal("0"))

    max_plies: int = Field(default=100)
    status: MarkerStatus = Field(default=MarkerStatus.draft, index=True)
    notes: Optional[str] = None

    sizes: List["MarkerSize"] = Relationship(
        back_populates="marker",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class MarkerSize(SQLModel, table=True):
    """How many garments of one size sit in a single ply of this marker."""

    __tablename__ = "marker_size"
    __table_args__ = (
        UniqueConstraint("marker_id", "size_label", name="uq_marker_size_label"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    marker_id: int = Field(foreign_key="marker.id", index=True)
    size_label: str = Field(max_length=20, index=True)
    quantity: int = Field(default=0)

    marker: Optional[Marker] = Relationship(back_populates="sizes")


class CutPlan(TimestampMixin, table=True):
    """A solved spreading plan: which markers, how many plies each."""

    __tablename__ = "cut_plan"
    __table_args__ = (UniqueConstraint("plan_number", name="uq_cut_plan_number"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    plan_number: str = Field(index=True)
    style_id: int = Field(foreign_key="style.id", index=True)
    sales_order_id: Optional[int] = Field(
        default=None, foreign_key="sales_order.id", index=True
    )
    cut_order_id: Optional[int] = Field(
        default=None, foreign_key="cut_order.id", index=True
    )
    fabric_material_id: Optional[int] = Field(
        default=None, foreign_key="material.id", index=True
    )
    width_cm: Decimal = quantity_field(default=Decimal("0"))
    max_plies: int = Field(default=100)

    status: CutPlanStatus = Field(default=CutPlanStatus.draft, index=True)

    # Solution summary.
    total_fabric_cm: Decimal = quantity_field(default=Decimal("0"))
    total_plies: int = Field(default=0)
    lay_count: int = Field(default=0)
    required_pieces: int = Field(default=0)
    planned_pieces: int = Field(default=0)
    overcut_pieces: int = Field(default=0)
    # Weighted marker efficiency across the lays actually chosen.
    weighted_efficiency_pct: Decimal = rate_field(default=Decimal("0"))
    # BOM-derived requirement for the same garments, for comparison.
    bom_fabric_cm: Optional[Decimal] = quantity_field(default=None, nullable=True)

    algorithm: str = Field(default="greedy-coverage+ply-reduction", max_length=60)
    notes: Optional[str] = None

    lays: List["CutPlanLay"] = Relationship(
        back_populates="plan",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "CutPlanLay.sequence",
        },
    )
    demands: List["CutPlanDemand"] = Relationship(
        back_populates="plan",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class CutPlanLay(SQLModel, table=True):
    """One spread: a marker laid ``plies`` deep."""

    __tablename__ = "cut_plan_lay"

    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="cut_plan.id", index=True)
    marker_id: int = Field(foreign_key="marker.id", index=True)
    sequence: int = Field(default=0)
    plies: int = Field(default=0)
    fabric_cm: Decimal = quantity_field(default=Decimal("0"))
    pieces: int = Field(default=0)

    plan: Optional[CutPlan] = Relationship(back_populates="lays")


class CutPlanDemand(SQLModel, table=True):
    """Required versus planned per size — where the overcut actually sits."""

    __tablename__ = "cut_plan_demand"

    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="cut_plan.id", index=True)
    size_label: str = Field(max_length=20)
    required_qty: int = Field(default=0)
    planned_qty: int = Field(default=0)
    overcut_qty: int = Field(default=0)

    plan: Optional[CutPlan] = Relationship(back_populates="demands")


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class MarkerSizeInput(SQLModel):
    size_label: str
    quantity: int


class MarkerCreate(SQLModel):
    marker_code: str
    style_id: int
    width_cm: Decimal
    length_cm: Decimal
    pattern_area_cm2: Decimal
    bom_version_id: Optional[int] = None
    max_plies: int = 100
    notes: Optional[str] = None
    sizes: List[MarkerSizeInput] = []


class MarkerSizeRead(SQLModel):
    size_label: str
    quantity: int


class MarkerRead(SQLModel):
    id: int
    marker_code: str
    style_id: int
    bom_version_id: Optional[int]
    width_cm: Decimal
    length_cm: Decimal
    pattern_area_cm2: Decimal
    efficiency_pct: Decimal
    max_plies: int
    status: MarkerStatus
    notes: Optional[str]
    pieces_per_ply: int
    sizes: List[MarkerSizeRead]


class SizeDemandInput(SQLModel):
    size_label: str
    quantity: int


class CutPlanCreate(SQLModel):
    """Solve a lay plan.

    ``sizes`` may be given explicitly, or omitted when ``sales_order_id`` is
    supplied — in which case the order's confirmed size matrix is the demand.
    """

    style_id: int
    width_cm: Decimal
    sales_order_id: Optional[int] = None
    cut_order_id: Optional[int] = None
    fabric_material_id: Optional[int] = None
    marker_ids: List[int] = []
    sizes: List[SizeDemandInput] = []
    max_plies: Optional[int] = None
    allow_overcut: bool = True
    notes: Optional[str] = None


class CutPlanLayRead(SQLModel):
    id: int
    sequence: int
    marker_id: int
    marker_code: Optional[str] = None
    plies: int
    fabric_cm: Decimal
    fabric_m: Decimal
    pieces: int
    efficiency_pct: Decimal


class CutPlanDemandRead(SQLModel):
    size_label: str
    required_qty: int
    planned_qty: int
    overcut_qty: int


class CutPlanRead(SQLModel):
    id: int
    plan_number: str
    style_id: int
    sales_order_id: Optional[int]
    cut_order_id: Optional[int]
    fabric_material_id: Optional[int]
    width_cm: Decimal
    max_plies: int
    status: CutPlanStatus
    total_fabric_cm: Decimal
    total_fabric_m: Decimal
    total_plies: int
    lay_count: int
    required_pieces: int
    planned_pieces: int
    overcut_pieces: int
    overcut_pct: Decimal
    weighted_efficiency_pct: Decimal
    bom_fabric_cm: Optional[Decimal]
    bom_fabric_m: Optional[Decimal]
    # Positive means the marker plan beats the BOM estimate.
    saving_vs_bom_m: Optional[Decimal]
    algorithm: str
    notes: Optional[str]
    lays: List[CutPlanLayRead]
    demands: List[CutPlanDemandRead]
