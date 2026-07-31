"""Module 6 — Costing: versioned style cost sheets + order profitability.

A cost sheet is a versioned specification like the BOM (locked-in decision):
immutable versions, downstream references to a specific version. The per-garment
cost builds up as:

    material/trim lines (qty × rate × (1+wastage))
      + SAM sewing cost (sam × cost/min ÷ efficiency)      (6.4)
      + overhead (overhead% of the above)
      = total cost
    selling price = total cost ÷ (1 − margin%)              (6.5)
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import money_field, quantity_field, rate_field


class CostSheetStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    superseded = "superseded"


class CostCategory(str, Enum):
    material = "material"
    trim = "trim"
    sewing = "sewing"
    overhead = "overhead"
    other = "other"


class CostSheetVersion(TimestampMixin, table=True):
    __tablename__ = "cost_sheet_version"

    id: Optional[int] = Field(default=None, primary_key=True)
    style_id: int = Field(foreign_key="style.id", index=True)
    version_no: int = Field(index=True)
    status: CostSheetStatus = Field(default=CostSheetStatus.draft, index=True)
    base_size: Optional[str] = None
    currency: str = "USD"

    # SAM sewing inputs (6.4).
    sam: Decimal = rate_field(default=Decimal("0"))
    sewing_cost_per_min: Decimal = money_field(default=Decimal("0"))
    sewing_efficiency_pct: Decimal = rate_field(default=Decimal("100"))

    overhead_pct: Decimal = rate_field(default=Decimal("0"))
    margin_pct: Decimal = rate_field(default=Decimal("0"))
    notes: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None

    lines: List["CostLine"] = Relationship(
        back_populates="cost_sheet",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class CostLine(SQLModel, table=True):
    __tablename__ = "cost_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    cost_sheet_version_id: int = Field(foreign_key="cost_sheet_version.id", index=True)
    category: CostCategory = CostCategory.material
    description: Optional[str] = None
    material_id: Optional[int] = Field(default=None, foreign_key="material.id")
    quantity: Decimal = rate_field(default=Decimal("0"))
    rate: Decimal = money_field(default=Decimal("0"))
    wastage_pct: Decimal = rate_field(default=Decimal("0"))

    cost_sheet: Optional[CostSheetVersion] = Relationship(back_populates="lines")


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class CostLineInput(SQLModel):
    category: CostCategory = CostCategory.material
    description: Optional[str] = None
    material_id: Optional[int] = None
    quantity: Decimal = Decimal("0")
    rate: Decimal = Decimal("0")
    wastage_pct: Decimal = Decimal("0")


class CostSheetCreate(SQLModel):
    base_size: Optional[str] = None
    currency: str = "USD"
    sam: Decimal = Decimal("0")
    sewing_cost_per_min: Decimal = Decimal("0")
    sewing_efficiency_pct: Decimal = Decimal("100")
    overhead_pct: Decimal = Decimal("0")
    margin_pct: Decimal = Decimal("0")
    notes: Optional[str] = None
    lines: List[CostLineInput] = []


class CostLineRead(SQLModel):
    id: int
    category: CostCategory
    description: Optional[str]
    material_id: Optional[int]
    quantity: Decimal
    rate: Decimal
    wastage_pct: Decimal
    amount: Decimal


class CostSheetRead(SQLModel):
    id: int
    style_id: int
    version_no: int
    status: CostSheetStatus
    base_size: Optional[str]
    currency: str
    sam: Decimal
    sewing_cost_per_min: Decimal
    sewing_efficiency_pct: Decimal
    overhead_pct: Decimal
    margin_pct: Decimal
    lines: List[CostLineRead]
    # Computed roll-up
    material_cost: Decimal
    sewing_cost: Decimal
    overhead_cost: Decimal
    total_cost: Decimal
    selling_price: Decimal
    margin_amount: Decimal


class OrderProfitabilityRead(SQLModel):
    sales_order_id: int
    order_number: str
    total_quantity: int
    revenue: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    profit: Decimal
    margin_pct: Decimal
    cost_sheet_version_id: Optional[int]


# --------------------------------------------------------------------------- #
# 6.8 Actual costing and variance analysis
#
# The cost sheet above is the *standard*. What the factory really consumed is
# already captured elsewhere — valued stock issues against cut orders, sewing
# minutes on daily output, piece-rate earnings, subcontract charges. A run
# gathers those actuals for one sales order, freezes them next to the standard,
# and decomposes the difference into the classic variances so the gap is
# explained rather than merely observed.
# --------------------------------------------------------------------------- #
class VarianceType(str, Enum):
    material_price = "material_price"
    material_usage = "material_usage"
    labour_rate = "labour_rate"
    labour_efficiency = "labour_efficiency"
    overhead = "overhead"
    subcontract = "subcontract"


class ActualCostRunStatus(str, Enum):
    draft = "draft"
    posted = "posted"


class ActualCostRun(TimestampMixin, table=True):
    """Frozen actual-vs-standard analysis for one sales order.

    A run is a snapshot, not a live view: once posted its numbers are the ones
    the journals were built from, so re-running later cannot retrospectively
    change what finance already booked.
    """

    __tablename__ = "actual_cost_run"
    __table_args__ = (
        UniqueConstraint("run_number", name="uq_actual_cost_run_number"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    run_number: str = Field(index=True)
    sales_order_id: int = Field(foreign_key="sales_order.id", index=True)
    cost_sheet_version_id: Optional[int] = Field(
        default=None, foreign_key="cost_sheet_version.id", index=True
    )
    status: ActualCostRunStatus = Field(default=ActualCostRunStatus.draft, index=True)
    currency: str = "USD"
    as_of: date

    # Volume the analysis is scaled to.
    produced_qty: int = 0

    # Standard side (unit rates × produced qty).
    std_material_cost: Decimal = money_field(default=Decimal("0"))
    std_labour_cost: Decimal = money_field(default=Decimal("0"))
    std_overhead_cost: Decimal = money_field(default=Decimal("0"))
    std_total_cost: Decimal = money_field(default=Decimal("0"))

    # Actual side.
    actual_material_cost: Decimal = money_field(default=Decimal("0"))
    actual_labour_cost: Decimal = money_field(default=Decimal("0"))
    actual_overhead_cost: Decimal = money_field(default=Decimal("0"))
    actual_subcontract_cost: Decimal = money_field(default=Decimal("0"))
    actual_total_cost: Decimal = money_field(default=Decimal("0"))

    # Drivers kept for auditability — a variance is meaningless without them.
    std_material_qty: Decimal = quantity_field(default=Decimal("0"))
    actual_material_qty: Decimal = quantity_field(default=Decimal("0"))
    std_minutes: Decimal = quantity_field(default=Decimal("0"))
    actual_minutes: Decimal = quantity_field(default=Decimal("0"))
    std_rate_per_min: Decimal = rate_field(default=Decimal("0"))
    actual_rate_per_min: Decimal = rate_field(default=Decimal("0"))

    journal_entry_id: Optional[int] = Field(default=None, index=True)
    posted_at: Optional[str] = None
    posted_by: Optional[str] = None
    notes: Optional[str] = None

    variances: List["CostVariance"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class CostVariance(SQLModel, table=True):
    """One decomposed variance line belonging to a run.

    ``amount`` follows the accounting sign convention: positive means the
    actual exceeded the standard (adverse, a debit), negative means favourable.
    """

    __tablename__ = "cost_variance"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="actual_cost_run.id", index=True)
    variance_type: VarianceType = Field(index=True)
    standard_amount: Decimal = money_field(default=Decimal("0"))
    actual_amount: Decimal = money_field(default=Decimal("0"))
    amount: Decimal = money_field(default=Decimal("0"))
    explanation: Optional[str] = None

    run: Optional[ActualCostRun] = Relationship(back_populates="variances")


# ------------------------------ API payloads ------------------------------- #
class ActualCostRunCreate(SQLModel):
    """Inputs the shop floor cannot supply on its own.

    Everything else is derived from captured operational data. The two
    overrides exist because actual wage rates and absorbed overhead are
    finance inputs, not production ones.
    """

    as_of: Optional[date] = None
    actual_rate_per_min: Optional[Decimal] = None
    overhead_absorbed: Optional[Decimal] = None
    include_subcontract: bool = True
    notes: Optional[str] = None


class CostVarianceRead(SQLModel):
    id: int
    variance_type: VarianceType
    standard_amount: Decimal
    actual_amount: Decimal
    amount: Decimal
    favourable: bool
    explanation: Optional[str]


class ActualCostRunRead(SQLModel):
    id: int
    run_number: str
    sales_order_id: int
    order_number: Optional[str] = None
    cost_sheet_version_id: Optional[int]
    status: ActualCostRunStatus
    currency: str
    as_of: date
    produced_qty: int

    std_material_cost: Decimal
    std_labour_cost: Decimal
    std_overhead_cost: Decimal
    std_total_cost: Decimal

    actual_material_cost: Decimal
    actual_labour_cost: Decimal
    actual_overhead_cost: Decimal
    actual_subcontract_cost: Decimal
    actual_total_cost: Decimal

    std_material_qty: Decimal
    actual_material_qty: Decimal
    std_minutes: Decimal
    actual_minutes: Decimal
    std_rate_per_min: Decimal
    actual_rate_per_min: Decimal

    total_variance: Decimal
    unit_std_cost: Decimal
    unit_actual_cost: Decimal
    journal_entry_id: Optional[int]
    posted_at: Optional[str]
    posted_by: Optional[str]
    notes: Optional[str]
    variances: List[CostVarianceRead]
