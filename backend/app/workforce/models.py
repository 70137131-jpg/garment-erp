"""Workforce: employee register, daily attendance, and piece-rate work.

Attendance is one row per employee per day (upserted by the day sheet).
Piece work stores the rate as entered — a later rate change never rewrites
what an operator already earned.
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import field_validator
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import money_field


class Department(str, Enum):
    cutting = "cutting"
    sewing = "sewing"
    finishing = "finishing"
    quality = "quality"
    stores = "stores"
    maintenance = "maintenance"
    admin = "admin"


class AttendanceStatus(str, Enum):
    present = "present"
    absent = "absent"
    leave = "leave"
    half_day = "half_day"
    holiday = "holiday"


class Employee(TimestampMixin, table=True):
    __tablename__ = "employee"

    id: Optional[int] = Field(default=None, primary_key=True)
    employee_number: str = Field(index=True, unique=True, max_length=32)
    name: str = Field(max_length=120)
    designation: str = Field(max_length=80)  # e.g. Operator, Helper, Checker
    department: Department = Field(index=True)
    line: Optional[str] = Field(default=None, max_length=40, index=True)
    joined_date: Optional[date] = None
    daily_wage: Decimal = money_field(default=Decimal("0"))
    piece_rate_worker: bool = Field(default=False)
    active: bool = Field(default=True, index=True)


class AttendanceRecord(TimestampMixin, table=True):
    __tablename__ = "attendance_record"
    __table_args__ = (
        UniqueConstraint("employee_id", "attendance_date", name="uq_attendance_employee_day"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    employee_id: int = Field(foreign_key="employee.id", index=True)
    attendance_date: date = Field(index=True)
    status: AttendanceStatus
    in_time: Optional[str] = Field(default=None, max_length=5)   # "HH:MM"
    out_time: Optional[str] = Field(default=None, max_length=5)
    overtime_minutes: int = Field(default=0)
    notes: Optional[str] = Field(default=None, max_length=300)
    marked_by: Optional[str] = Field(default=None, max_length=320)


class PieceRate(TimestampMixin, table=True):
    """Rate per garment operation, optionally per style."""

    __tablename__ = "piece_rate"
    __table_args__ = (
        UniqueConstraint("style_id", "operation", name="uq_piece_rate_style_operation"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    style_id: Optional[int] = Field(default=None, foreign_key="style.id", index=True)
    operation: str = Field(index=True, max_length=80)
    rate: Decimal = money_field()
    active: bool = Field(default=True)


class PieceWorkRecord(TimestampMixin, table=True):
    __tablename__ = "piece_work_record"

    id: Optional[int] = Field(default=None, primary_key=True)
    employee_id: int = Field(foreign_key="employee.id", index=True)
    work_date: date = Field(index=True)
    style_id: Optional[int] = Field(default=None, foreign_key="style.id", index=True)
    operation: str = Field(max_length=80)
    pieces: int
    rate: Decimal = money_field()  # snapshot at entry time
    entered_by: Optional[str] = Field(default=None, max_length=320)


# ------------------------------ API models --------------------------------- #

_TIME_ERROR = "Times must be HH:MM (24h)"


def _valid_time(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(_TIME_ERROR)
    hours, minutes = int(parts[0]), int(parts[1])
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(_TIME_ERROR)
    return f"{hours:02d}:{minutes:02d}"


class EmployeeCreate(SQLModel):
    name: str
    designation: str
    department: Department
    line: Optional[str] = None
    joined_date: Optional[date] = None
    daily_wage: Decimal = Decimal("0")
    piece_rate_worker: bool = False

    @field_validator("name", "designation")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field is required")
        return value

    @field_validator("daily_wage")
    @classmethod
    def non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("Wage cannot be negative")
        return value


class EmployeeUpdate(SQLModel):
    name: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[Department] = None
    line: Optional[str] = None
    daily_wage: Optional[Decimal] = None
    piece_rate_worker: Optional[bool] = None
    active: Optional[bool] = None


class EmployeeRead(SQLModel):
    id: int
    employee_number: str
    name: str
    designation: str
    department: Department
    line: Optional[str]
    joined_date: Optional[date]
    daily_wage: Decimal
    piece_rate_worker: bool
    active: bool


class AttendanceEntry(SQLModel):
    employee_id: int
    status: AttendanceStatus
    in_time: Optional[str] = None
    out_time: Optional[str] = None
    overtime_minutes: int = 0
    notes: Optional[str] = None

    @field_validator("in_time", "out_time")
    @classmethod
    def check_time(cls, value: Optional[str]) -> Optional[str]:
        return _valid_time(value)

    @field_validator("overtime_minutes")
    @classmethod
    def sane_overtime(cls, value: int) -> int:
        if value < 0 or value > 12 * 60:
            raise ValueError("Overtime must be between 0 and 720 minutes")
        return value


class AttendanceSheetRequest(SQLModel):
    attendance_date: date
    entries: List[AttendanceEntry]

    @field_validator("entries")
    @classmethod
    def entries_required(cls, value: List[AttendanceEntry]) -> List[AttendanceEntry]:
        if not value:
            raise ValueError("At least one entry is required")
        seen: set[int] = set()
        for entry in value:
            if entry.employee_id in seen:
                raise ValueError(f"Duplicate employee {entry.employee_id} in sheet")
            seen.add(entry.employee_id)
        return value


class AttendanceRecordRead(SQLModel):
    id: int
    employee_id: int
    attendance_date: date
    status: AttendanceStatus
    in_time: Optional[str]
    out_time: Optional[str]
    overtime_minutes: int
    notes: Optional[str]


class DaySheetRow(SQLModel):
    employee: EmployeeRead
    record: Optional[AttendanceRecordRead]


class MonthlySummaryRow(SQLModel):
    employee: EmployeeRead
    present: int
    half_days: int
    absent: int
    leave: int
    holidays: int
    overtime_minutes: int


class PieceRateCreate(SQLModel):
    style_id: Optional[int] = None
    operation: str
    rate: Decimal

    @field_validator("operation")
    @classmethod
    def operation_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Operation is required")
        return value

    @field_validator("rate")
    @classmethod
    def positive_rate(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("Rate must be positive")
        return value


class PieceRateRead(SQLModel):
    id: int
    style_id: Optional[int]
    operation: str
    rate: Decimal
    active: bool


class PieceWorkEntry(SQLModel):
    employee_id: int
    style_id: Optional[int] = None
    operation: str
    pieces: int

    @field_validator("pieces")
    @classmethod
    def positive_pieces(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Pieces must be positive")
        return value


class PieceWorkSheetRequest(SQLModel):
    work_date: date
    entries: List[PieceWorkEntry]


class PieceWorkRecordRead(SQLModel):
    id: int
    employee_id: int
    work_date: date
    style_id: Optional[int]
    operation: str
    pieces: int
    rate: Decimal
    amount: Decimal


class EarningsRow(SQLModel):
    employee: EmployeeRead
    days_present: int
    half_days: int
    overtime_minutes: int
    attendance_amount: Decimal
    piece_pieces: int
    piece_amount: Decimal
    total_amount: Decimal
