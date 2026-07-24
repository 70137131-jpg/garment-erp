"""Workforce endpoints: employees, attendance sheets, piece rates, earnings.

Reads require the workforce:read permission (HR-sensitive — not part of the
blanket read set). Mutations are restricted to floor supervisors and admins.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.numbering import next_document_number
from ..kernel.rbac import Permission, Role, require_permissions, require_roles
from .models import (
    AttendanceRecord,
    AttendanceRecordRead,
    AttendanceSheetRequest,
    AttendanceStatus,
    DaySheetRow,
    EarningsRow,
    Employee,
    EmployeeCreate,
    EmployeeRead,
    EmployeeUpdate,
    MonthlySummaryRow,
    PieceRate,
    PieceRateCreate,
    PieceRateRead,
    PieceWorkRecord,
    PieceWorkRecordRead,
    PieceWorkSheetRequest,
)

router = APIRouter(
    prefix="/workforce",
    tags=["workforce"],
    dependencies=[Depends(require_permissions(Permission.workforce_read))],
)

MANAGE_ROLES = (Role.sewing_supervisor, Role.cutting_supervisor)


def _employee_read(employee: Employee) -> EmployeeRead:
    return EmployeeRead(
        id=employee.id,
        employee_number=employee.employee_number,
        name=employee.name,
        designation=employee.designation,
        department=employee.department,
        line=employee.line,
        joined_date=employee.joined_date,
        daily_wage=employee.daily_wage,
        piece_rate_worker=employee.piece_rate_worker,
        active=employee.active,
    )


def _record_read(record: AttendanceRecord) -> AttendanceRecordRead:
    return AttendanceRecordRead(
        id=record.id,
        employee_id=record.employee_id,
        attendance_date=record.attendance_date,
        status=record.status,
        in_time=record.in_time,
        out_time=record.out_time,
        overtime_minutes=record.overtime_minutes,
        notes=record.notes,
    )


# ------------------------------- Employees --------------------------------- #

@router.get("/employees", response_model=List[EmployeeRead])
def list_employees(
    department: Optional[str] = None,
    active: Optional[bool] = None,
    q: Optional[str] = None,
    session: Session = Depends(get_session),
):
    stmt = select(Employee).order_by(Employee.employee_number)
    if department is not None:
        stmt = stmt.where(Employee.department == department)
    if active is not None:
        stmt = stmt.where(Employee.active == active)
    employees = session.exec(stmt).all()
    if q:
        needle = q.strip().lower()
        employees = [
            e for e in employees
            if needle in e.name.lower() or needle in e.employee_number.lower()
        ]
    return [_employee_read(e) for e in employees]


@router.post("/employees", response_model=EmployeeRead, status_code=201)
def create_employee(
    payload: EmployeeCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(*MANAGE_ROLES)),
):
    number = next_document_number(session, "EMPLOYEE", "EMP")
    employee = Employee(**payload.model_dump(), employee_number=number)
    session.add(employee)
    session.commit()
    session.refresh(employee)
    return _employee_read(employee)


@router.patch("/employees/{employee_id}", response_model=EmployeeRead)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(*MANAGE_ROLES)),
):
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(employee, field, value)
    session.add(employee)
    session.commit()
    session.refresh(employee)
    return _employee_read(employee)


# ------------------------------- Attendance -------------------------------- #

@router.post("/attendance", response_model=List[AttendanceRecordRead])
def mark_attendance(
    payload: AttendanceSheetRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(*MANAGE_ROLES)),
):
    """Upsert the day sheet: one record per employee per day."""
    if payload.attendance_date > date.today():
        raise HTTPException(status_code=422, detail="Cannot mark attendance for a future date")
    employee_ids = [entry.employee_id for entry in payload.entries]
    employees = {
        e.id: e
        for e in session.exec(select(Employee).where(Employee.id.in_(employee_ids))).all()
    }
    missing = [str(i) for i in employee_ids if i not in employees]
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown employees: {', '.join(missing)}")
    inactive = [employees[i].employee_number for i in employee_ids if not employees[i].active]
    if inactive:
        raise HTTPException(status_code=422, detail=f"Inactive employees: {', '.join(inactive)}")

    existing = {
        record.employee_id: record
        for record in session.exec(
            select(AttendanceRecord).where(
                AttendanceRecord.attendance_date == payload.attendance_date,
                AttendanceRecord.employee_id.in_(employee_ids),
            )
        ).all()
    }
    saved: List[AttendanceRecord] = []
    for entry in payload.entries:
        record = existing.get(entry.employee_id)
        if record is None:
            record = AttendanceRecord(
                employee_id=entry.employee_id,
                attendance_date=payload.attendance_date,
                status=entry.status,
            )
        record.status = entry.status
        record.in_time = entry.in_time
        record.out_time = entry.out_time
        record.overtime_minutes = entry.overtime_minutes
        record.notes = (entry.notes or "").strip()[:300] or None
        record.marked_by = actor
        session.add(record)
        saved.append(record)
    session.commit()
    for record in saved:
        session.refresh(record)
    return [_record_read(record) for record in saved]


@router.get("/attendance/day", response_model=List[DaySheetRow])
def day_sheet(
    attendance_date: date = Query(...),
    department: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """Every active employee with their record for the day (or none yet)."""
    stmt = select(Employee).where(Employee.active == True).order_by(Employee.employee_number)  # noqa: E712
    if department is not None:
        stmt = stmt.where(Employee.department == department)
    employees = session.exec(stmt).all()
    records = {
        record.employee_id: record
        for record in session.exec(
            select(AttendanceRecord).where(AttendanceRecord.attendance_date == attendance_date)
        ).all()
    }
    return [
        DaySheetRow(
            employee=_employee_read(employee),
            record=_record_read(records[employee.id]) if employee.id in records else None,
        )
        for employee in employees
    ]


@router.get("/attendance/summary", response_model=List[MonthlySummaryRow])
def monthly_summary(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    session: Session = Depends(get_session),
):
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    employees = session.exec(
        select(Employee).where(Employee.active == True).order_by(Employee.employee_number)  # noqa: E712
    ).all()
    records = session.exec(
        select(AttendanceRecord).where(
            AttendanceRecord.attendance_date >= start,
            AttendanceRecord.attendance_date < end,
        )
    ).all()
    by_employee: dict[int, List[AttendanceRecord]] = {}
    for record in records:
        by_employee.setdefault(record.employee_id, []).append(record)

    rows: List[MonthlySummaryRow] = []
    for employee in employees:
        mine = by_employee.get(employee.id, [])
        rows.append(
            MonthlySummaryRow(
                employee=_employee_read(employee),
                present=sum(1 for r in mine if r.status == AttendanceStatus.present),
                half_days=sum(1 for r in mine if r.status == AttendanceStatus.half_day),
                absent=sum(1 for r in mine if r.status == AttendanceStatus.absent),
                leave=sum(1 for r in mine if r.status == AttendanceStatus.leave),
                holidays=sum(1 for r in mine if r.status == AttendanceStatus.holiday),
                overtime_minutes=sum(r.overtime_minutes for r in mine),
            )
        )
    return rows


# ------------------------------- Piece rates ------------------------------- #

@router.get("/piece-rates", response_model=List[PieceRateRead])
def list_piece_rates(
    style_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    stmt = select(PieceRate).where(PieceRate.active == True).order_by(PieceRate.operation)  # noqa: E712
    if style_id is not None:
        stmt = stmt.where(PieceRate.style_id == style_id)
    return [
        PieceRateRead(
            id=rate.id, style_id=rate.style_id, operation=rate.operation,
            rate=rate.rate, active=rate.active,
        )
        for rate in session.exec(stmt).all()
    ]


@router.post("/piece-rates", response_model=PieceRateRead, status_code=201)
def create_piece_rate(
    payload: PieceRateCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(*MANAGE_ROLES)),
):
    duplicate = session.exec(
        select(PieceRate).where(
            PieceRate.style_id == payload.style_id,
            PieceRate.operation == payload.operation,
            PieceRate.active == True,  # noqa: E712
        )
    ).first()
    if duplicate is not None:
        # Rate revision: retire the old rate; history stays on past work records.
        duplicate.active = False
        session.add(duplicate)
    rate = PieceRate(**payload.model_dump())
    session.add(rate)
    session.commit()
    session.refresh(rate)
    return PieceRateRead(
        id=rate.id, style_id=rate.style_id, operation=rate.operation,
        rate=rate.rate, active=rate.active,
    )


def _resolve_rate(session: Session, style_id: Optional[int], operation: str) -> PieceRate:
    """Style-specific rate wins; fall back to the generic (style-less) rate."""
    if style_id is not None:
        specific = session.exec(
            select(PieceRate).where(
                PieceRate.style_id == style_id,
                PieceRate.operation == operation,
                PieceRate.active == True,  # noqa: E712
            )
        ).first()
        if specific is not None:
            return specific
    generic = session.exec(
        select(PieceRate).where(
            PieceRate.style_id.is_(None),
            PieceRate.operation == operation,
            PieceRate.active == True,  # noqa: E712
        )
    ).first()
    if generic is None:
        raise HTTPException(
            status_code=422,
            detail=f"No active piece rate for operation '{operation}'",
        )
    return generic


@router.post("/piece-work", response_model=List[PieceWorkRecordRead], status_code=201)
def record_piece_work(
    payload: PieceWorkSheetRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(*MANAGE_ROLES)),
):
    if payload.work_date > date.today():
        raise HTTPException(status_code=422, detail="Cannot record piece work for a future date")
    if not payload.entries:
        raise HTTPException(status_code=422, detail="At least one entry is required")
    employee_ids = {entry.employee_id for entry in payload.entries}
    employees = {
        e.id: e
        for e in session.exec(select(Employee).where(Employee.id.in_(employee_ids))).all()
    }
    missing = [str(i) for i in employee_ids if i not in employees]
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown employees: {', '.join(missing)}")

    saved: List[PieceWorkRecord] = []
    for entry in payload.entries:
        rate = _resolve_rate(session, entry.style_id, entry.operation.strip())
        record = PieceWorkRecord(
            employee_id=entry.employee_id,
            work_date=payload.work_date,
            style_id=entry.style_id,
            operation=entry.operation.strip(),
            pieces=entry.pieces,
            rate=rate.rate,
            entered_by=actor,
        )
        session.add(record)
        saved.append(record)
    session.commit()
    for record in saved:
        session.refresh(record)
    return [
        PieceWorkRecordRead(
            id=record.id, employee_id=record.employee_id, work_date=record.work_date,
            style_id=record.style_id, operation=record.operation, pieces=record.pieces,
            rate=record.rate, amount=record.rate * record.pieces,
        )
        for record in saved
    ]


# -------------------------------- Earnings --------------------------------- #

@router.get("/earnings", response_model=List[EarningsRow])
def earnings(
    date_from: date = Query(...),
    date_to: date = Query(...),
    session: Session = Depends(get_session),
):
    """Per-employee earnings for a period: attendance wages + piece work.

    Daily-wage staff earn wage × (present + 0.5 × half days); piece-rate
    workers earn their recorded pieces × snapshot rate. Overtime minutes are
    reported for payroll to price according to factory policy.
    """
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")
    employees = session.exec(
        select(Employee).order_by(Employee.employee_number)
    ).all()
    attendance = session.exec(
        select(AttendanceRecord).where(
            AttendanceRecord.attendance_date >= date_from,
            AttendanceRecord.attendance_date <= date_to,
        )
    ).all()
    piece_work = session.exec(
        select(PieceWorkRecord).where(
            PieceWorkRecord.work_date >= date_from,
            PieceWorkRecord.work_date <= date_to,
        )
    ).all()
    attendance_by: dict[int, List[AttendanceRecord]] = {}
    for record in attendance:
        attendance_by.setdefault(record.employee_id, []).append(record)
    piece_by: dict[int, List[PieceWorkRecord]] = {}
    for record in piece_work:
        piece_by.setdefault(record.employee_id, []).append(record)

    rows: List[EarningsRow] = []
    for employee in employees:
        mine = attendance_by.get(employee.id, [])
        work = piece_by.get(employee.id, [])
        if not mine and not work and not employee.active:
            continue
        present = sum(1 for r in mine if r.status == AttendanceStatus.present)
        half = sum(1 for r in mine if r.status == AttendanceStatus.half_day)
        overtime = sum(r.overtime_minutes for r in mine)
        attendance_amount = Decimal("0")
        if not employee.piece_rate_worker:
            attendance_amount = (
                employee.daily_wage * (Decimal(present) + Decimal("0.5") * Decimal(half))
            ).quantize(Decimal("0.01"))
        piece_amount = sum(
            (record.rate * record.pieces for record in work), Decimal("0")
        ).quantize(Decimal("0.01"))
        rows.append(
            EarningsRow(
                employee=_employee_read(employee),
                days_present=present,
                half_days=half,
                overtime_minutes=overtime,
                attendance_amount=attendance_amount,
                piece_pieces=sum(record.pieces for record in work),
                piece_amount=piece_amount,
                total_amount=(attendance_amount + piece_amount).quantize(Decimal("0.01")),
            )
        )
    return rows
