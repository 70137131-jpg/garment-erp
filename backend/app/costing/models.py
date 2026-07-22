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

from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import money_field, rate_field


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
