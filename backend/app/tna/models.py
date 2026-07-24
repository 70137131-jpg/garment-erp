"""Time & Action calendars — the merchandiser's execution plan per order.

A template holds the standard milestone ladder as day-offsets from the
ex-factory date (negative = days before). Applying a template to a confirmed
sales order materializes dated milestones; actuals are recorded as work
completes, and lateness is derived, never stored.
"""

from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import field_validator
from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin


class TnaMilestoneStatus(str, Enum):
    pending = "pending"
    due_soon = "due_soon"      # within 3 days of planned
    late = "late"              # planned date passed, no actual
    done = "done"              # actual on/before planned
    done_late = "done_late"    # actual after planned


class TnaTemplate(TimestampMixin, table=True):
    __tablename__ = "tna_template"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, max_length=120)
    active: bool = Field(default=True)

    steps: List["TnaTemplateStep"] = Relationship(
        back_populates="template",
        sa_relationship_kwargs={
            "order_by": "TnaTemplateStep.sequence",
            "cascade": "all, delete-orphan",
        },
    )


class TnaTemplateStep(SQLModel, table=True):
    __tablename__ = "tna_template_step"

    id: Optional[int] = Field(default=None, primary_key=True)
    template_id: int = Field(foreign_key="tna_template.id", index=True)
    sequence: int
    name: str = Field(max_length=120)
    # Days relative to ex-factory: -45 = 45 days before, 0 = ex-factory day.
    offset_days: int
    owner_role: Optional[str] = Field(default=None, max_length=64)

    template: Optional[TnaTemplate] = Relationship(back_populates="steps")


class TnaMilestone(TimestampMixin, table=True):
    __tablename__ = "tna_milestone"

    id: Optional[int] = Field(default=None, primary_key=True)
    sales_order_id: int = Field(foreign_key="sales_order.id", index=True)
    sequence: int
    name: str = Field(max_length=120)
    planned_date: date = Field(index=True)
    actual_date: Optional[date] = Field(default=None)
    owner_role: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = Field(default=None, max_length=500)


# ------------------------------ API models --------------------------------- #

class TnaTemplateStepCreate(SQLModel):
    sequence: int
    name: str
    offset_days: int
    owner_role: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_required(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError("Step name must be 1-120 characters")
        return value


class TnaTemplateCreate(SQLModel):
    name: str
    steps: List[TnaTemplateStepCreate]

    @field_validator("name")
    @classmethod
    def name_required(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError("Template name must be 1-120 characters")
        return value

    @field_validator("steps")
    @classmethod
    def steps_required(cls, value: List[TnaTemplateStepCreate]) -> List[TnaTemplateStepCreate]:
        if not value:
            raise ValueError("A template needs at least one step")
        return value


class TnaTemplateStepRead(SQLModel):
    id: int
    sequence: int
    name: str
    offset_days: int
    owner_role: Optional[str]


class TnaTemplateRead(SQLModel):
    id: int
    name: str
    active: bool
    steps: List[TnaTemplateStepRead] = []


class TnaApplyRequest(SQLModel):
    template_id: int
    ex_factory_date: date
    replace: bool = False


class TnaMilestoneUpdate(SQLModel):
    planned_date: Optional[date] = None
    actual_date: Optional[date] = None
    clear_actual: bool = False
    notes: Optional[str] = None


class TnaMilestoneRead(SQLModel):
    id: int
    sales_order_id: int
    sequence: int
    name: str
    planned_date: date
    actual_date: Optional[date]
    owner_role: Optional[str]
    notes: Optional[str]
    status: TnaMilestoneStatus
    slip_days: int  # positive = late by N days (open or closed late)


class TnaOrderBoardRow(SQLModel):
    sales_order_id: int
    order_number: str
    customer_id: int
    order_status: str
    ex_factory_date: Optional[date]
    total_milestones: int
    completed: int
    late: int
    worst_slip_days: int
    next_milestone: Optional[str]
    next_planned_date: Optional[date]
