"""Module 7 — Quality. Incoming four-point fabric inspection (7.1/7.2).

Inline DHU (7.3) and final AQL (7.4) are added in Phase 7 alongside this.

Four-point system: each defect scores 1–4 penalty points by size; the roll's
score is normalised to points per 100 square yards:

    points_per_100_sqyd = (total_points × 3600) / (length_yards × width_inches)

A roll scoring at or below the acceptance threshold (default 40) passes and
becomes ``available``; otherwise it is ``quarantined`` (7.5 — quality status on
inventory).
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import quantity_field


class InspectionResult(str, Enum):
    pending = "pending"
    passed = "passed"
    failed = "failed"


class FourPointInspection(TimestampMixin, table=True):
    __tablename__ = "four_point_inspection"

    id: Optional[int] = Field(default=None, primary_key=True)
    inspection_number: str = Field(index=True, unique=True)
    roll_id: int = Field(foreign_key="roll.id", index=True)
    inspected_length: Decimal = quantity_field(default=Decimal("0"))  # metres
    width_cm: Decimal = quantity_field(default=Decimal("0"))
    acceptance_threshold: Decimal = quantity_field(default=Decimal("40"))
    total_points: int = 0
    points_per_100sqyd: Decimal = quantity_field(default=Decimal("0"))
    result: InspectionResult = Field(default=InspectionResult.pending)
    inspected_date: Optional[date] = None
    inspector: Optional[str] = None

    defects: List["FourPointDefect"] = Relationship(
        back_populates="inspection",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class FourPointDefect(SQLModel, table=True):
    __tablename__ = "four_point_defect"

    id: Optional[int] = Field(default=None, primary_key=True)
    inspection_id: int = Field(foreign_key="four_point_inspection.id", index=True)
    description: Optional[str] = None
    defect_code: Optional[str] = Field(default=None, index=True)
    category: Optional[str] = Field(default=None, index=True)
    penalty_points: int = 0  # 1..4
    position_m: Optional[Decimal] = quantity_field(default=None, nullable=True)

    inspection: Optional[FourPointInspection] = Relationship(back_populates="defects")


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class DefectInput(SQLModel):
    description: Optional[str] = None
    defect_code: Optional[str] = None
    category: Optional[str] = None
    penalty_points: int
    position_m: Optional[Decimal] = None


class FourPointInspectionCreate(SQLModel):
    roll_id: int
    inspected_length: Optional[Decimal] = None  # defaults to roll length
    width_cm: Optional[Decimal] = None  # defaults to roll width
    acceptance_threshold: Decimal = Decimal("40")
    inspected_date: Optional[date] = None
    defects: List[DefectInput] = []


class DefectRead(SQLModel):
    description: Optional[str]
    defect_code: Optional[str] = None
    category: Optional[str] = None
    penalty_points: int
    position_m: Optional[Decimal]


class FourPointInspectionRead(SQLModel):
    id: int
    inspection_number: str
    roll_id: int
    inspected_length: Decimal
    width_cm: Decimal
    acceptance_threshold: Decimal
    total_points: int
    points_per_100sqyd: Decimal
    result: InspectionResult
    roll_status: str
    defects: List[DefectRead]


# --------------------------------------------------------------------------- #
# 7.3 Inline inspection — DHU (defects per hundred units)
# --------------------------------------------------------------------------- #
class InlineInspection(TimestampMixin, table=True):
    __tablename__ = "inline_inspection"

    id: Optional[int] = Field(default=None, primary_key=True)
    inspection_number: str = Field(index=True, unique=True)
    sewing_order_id: int = Field(foreign_key="sewing_order.id", index=True)
    inspected_date: Optional[date] = None
    units_checked: int = 0
    defects_found: int = 0
    dhu: Decimal = quantity_field(default=Decimal("0"))
    inspector: Optional[str] = None


class InlineInspectionCreate(SQLModel):
    sewing_order_id: int
    inspected_date: Optional[date] = None
    units_checked: int
    defects_found: int


class InlineInspectionRead(SQLModel):
    id: int
    inspection_number: str
    sewing_order_id: int
    units_checked: int
    defects_found: int
    dhu: Decimal


# --------------------------------------------------------------------------- #
# 7.4 Final inspection — AQL (gates shipment)
# --------------------------------------------------------------------------- #
class FinalInspection(TimestampMixin, table=True):
    __tablename__ = "final_inspection"

    id: Optional[int] = Field(default=None, primary_key=True)
    inspection_number: str = Field(index=True, unique=True)
    sales_order_id: int = Field(foreign_key="sales_order.id", index=True)
    lot_size: int = 0
    aql: Decimal = quantity_field(default=Decimal("2.5"))
    code_letter: str = ""
    sample_size: int = 0
    accept_number: int = 0
    reject_number: int = 0
    defects_found: int = 0
    result: InspectionResult = Field(default=InspectionResult.pending)
    inspected_date: Optional[date] = None
    inspector: Optional[str] = None


class FinalInspectionCreate(SQLModel):
    sales_order_id: int
    lot_size: int
    aql: Decimal = Decimal("2.5")
    defects_found: int
    inspected_date: Optional[date] = None


class FinalInspectionRead(SQLModel):
    id: int
    inspection_number: str
    sales_order_id: int
    lot_size: int
    aql: Decimal
    code_letter: str
    sample_size: int
    accept_number: int
    reject_number: int
    defects_found: int
    result: InspectionResult


class LabTestStatus(str, Enum):
    pending = "pending"
    passed = "passed"
    failed = "failed"
    cancelled = "cancelled"


class LabTest(TimestampMixin, table=True):
    __tablename__ = "lab_test"

    id: Optional[int] = Field(default=None, primary_key=True)
    test_number: str = Field(index=True, unique=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    roll_id: Optional[int] = Field(default=None, foreign_key="roll.id", index=True)
    supplier_id: Optional[int] = Field(default=None, foreign_key="supplier.id", index=True)
    test_type: str = Field(index=True)
    method: Optional[str] = None
    specification: Optional[str] = None
    measured_value: Optional[str] = None
    status: LabTestStatus = Field(default=LabTestStatus.pending, index=True)
    submitted_date: Optional[date] = None
    completed_date: Optional[date] = None
    laboratory: Optional[str] = None
    certificate_reference: Optional[str] = None
    tested_by: Optional[str] = None
    note: Optional[str] = None


class LabTestCreate(SQLModel):
    material_id: int
    roll_id: Optional[int] = None
    supplier_id: Optional[int] = None
    test_type: str
    method: Optional[str] = None
    specification: Optional[str] = None
    submitted_date: Optional[date] = None
    laboratory: Optional[str] = None
    note: Optional[str] = None


class LabTestComplete(SQLModel):
    passed: bool
    measured_value: str
    completed_date: Optional[date] = None
    certificate_reference: Optional[str] = None
    note: Optional[str] = None


class LabTestRead(SQLModel):
    id: int
    test_number: str
    material_id: int
    roll_id: Optional[int]
    supplier_id: Optional[int]
    test_type: str
    method: Optional[str]
    specification: Optional[str]
    measured_value: Optional[str]
    status: LabTestStatus
    submitted_date: Optional[date]
    completed_date: Optional[date]
    laboratory: Optional[str]
    certificate_reference: Optional[str]
    tested_by: Optional[str]
    note: Optional[str]


class DefectAnalyticsRead(SQLModel):
    defect_key: str
    occurrences: int
    penalty_points: int
    share_pct: Decimal
