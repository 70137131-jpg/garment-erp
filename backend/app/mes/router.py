from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.query import csv_download, page_bounds
from ..kernel.rbac import Role, require_roles
from ..planning.models import WorkCentre
from .models import (
    AndonBoardRead,
    AndonLine,
    DowntimeCategory,
    DowntimeEvent,
    DowntimeEventCreate,
    DowntimeEventRead,
    DowntimeReason,
    DowntimeReasonCreate,
    DowntimeReasonRead,
    Machine,
    MachineCreate,
    MachineRead,
    OeeSummaryRead,
    ShiftCountUpdate,
    ShiftLog,
    ShiftLogCreate,
    ShiftLogRead,
    ShiftLogStatus,
)
from .service import (
    MesError,
    add_downtime,
    aggregate_factors,
    close_shift_log,
    compute_oee,
    create_shift_log,
    downtime_pareto,
    oee_summary,
    resolve_ideal_seconds,
)

router = APIRouter(prefix="/mes", tags=["mes"])


# --------------------------------------------------------------------------- #
# Machines and downtime reasons
# --------------------------------------------------------------------------- #
@router.post("/machines", response_model=MachineRead, status_code=201)
def create_machine(
    payload: MachineCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner)),
):
    code = payload.code.strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="Machine code is required")
    if session.get(WorkCentre, payload.work_centre_id) is None:
        raise HTTPException(status_code=404, detail="Work centre not found")
    if session.exec(select(Machine).where(Machine.code == code)).first():
        raise HTTPException(status_code=409, detail="Machine code already exists")
    machine = Machine(**{**payload.model_dump(), "code": code})
    session.add(machine)
    session.commit()
    session.refresh(machine)
    return machine


@router.get("/machines", response_model=List[MachineRead])
def list_machines(
    session: Session = Depends(get_session),
    work_centre_id: Optional[int] = None,
):
    stmt = select(Machine)
    if work_centre_id is not None:
        stmt = stmt.where(Machine.work_centre_id == work_centre_id)
    return session.exec(stmt.order_by(Machine.code)).all()


@router.post("/downtime-reasons", response_model=DowntimeReasonRead, status_code=201)
def create_downtime_reason(
    payload: DowntimeReasonCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.planner, Role.sewing_supervisor)),
):
    code = payload.code.strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="Reason code is required")
    if session.exec(select(DowntimeReason).where(DowntimeReason.code == code)).first():
        raise HTTPException(status_code=409, detail="Reason code already exists")
    reason = DowntimeReason(**{**payload.model_dump(), "code": code})
    session.add(reason)
    session.commit()
    session.refresh(reason)
    return reason


@router.get("/downtime-reasons", response_model=List[DowntimeReasonRead])
def list_downtime_reasons(
    session: Session = Depends(get_session),
    category: Optional[DowntimeCategory] = None,
):
    stmt = select(DowntimeReason).where(DowntimeReason.active == True)  # noqa: E712
    if category is not None:
        stmt = stmt.where(DowntimeReason.category == category)
    return session.exec(stmt.order_by(DowntimeReason.code)).all()


# --------------------------------------------------------------------------- #
# Shift logs
# --------------------------------------------------------------------------- #
class _LogContext:
    """Batch-loaded lookups for rendering many shift logs.

    Work centres, machines and downtime reasons are small reference tables that
    were being fetched per row (and per downtime event). One query each covers
    a whole page of logs.
    """

    __slots__ = ("centres", "machines", "reasons")

    def __init__(self, session: Session, logs):
        logs = list(logs)
        self.centres = _by_id(session, WorkCentre, (l.work_centre_id for l in logs))
        self.machines = _by_id(session, Machine, (l.machine_id for l in logs))
        self.reasons = _by_id(
            session, DowntimeReason,
            (e.reason_id for l in logs for e in l.downtime),
        )


def _by_id(session: Session, model, ids) -> dict:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {r.id: r for r in session.exec(select(model).where(model.id.in_(ids))).all()}


def _log_read(
    session: Session, log: ShiftLog, ctx: Optional["_LogContext"] = None
) -> ShiftLogRead:
    if ctx is None:
        ctx = _LogContext(session, [log])
    centre = ctx.centres.get(log.work_centre_id)
    machine = ctx.machines.get(log.machine_id) if log.machine_id else None

    events: List[DowntimeEventRead] = []
    for event in log.downtime:
        reason = ctx.reasons.get(event.reason_id)
        events.append(
            DowntimeEventRead(
                id=event.id,
                reason_id=event.reason_id,
                reason_code=reason.code if reason else None,
                reason_description=reason.description if reason else None,
                category=reason.category if reason else None,
                planned=bool(reason.planned) if reason else False,
                minutes=event.minutes,
                note=event.note,
                recorded_by=event.recorded_by,
            )
        )

    data = {
        "id": log.id,
        "log_number": log.log_number,
        "work_centre_id": log.work_centre_id,
        "work_centre_code": centre.code if centre else None,
        "machine_id": log.machine_id,
        "machine_code": machine.code if machine else None,
        "sewing_order_id": log.sewing_order_id,
        "log_date": log.log_date,
        "shift": log.shift,
        "operators": log.operators,
        "planned_minutes": log.planned_minutes,
        "total_count": log.total_count,
        "good_count": log.good_count,
        "reject_count": log.reject_count,
        "ideal_cycle_seconds": log.ideal_cycle_seconds,
        "status": log.status,
        "closed_at": log.closed_at,
        "closed_by": log.closed_by,
        "notes": log.notes,
        "downtime": events,
    }

    if log.status == ShiftLogStatus.open:
        # Live preview so the floor sees where the shift is heading without
        # having to close it first. Not persisted — only closing freezes it.
        data.update(compute_oee(session, log))
    else:
        data.update({
            "planned_downtime_minutes": log.planned_downtime_minutes,
            "unplanned_downtime_minutes": log.unplanned_downtime_minutes,
            "run_minutes": log.run_minutes,
            "availability_pct": log.availability_pct,
            "performance_pct": log.performance_pct,
            "quality_pct": log.quality_pct,
            "oee_pct": log.oee_pct,
            "performance_capped": log.performance_capped,
        })
    return ShiftLogRead(**data)


@router.post("/shift-logs", response_model=ShiftLogRead, status_code=201)
def open_shift_log(
    payload: ShiftLogCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.sewing_supervisor, Role.cutting_supervisor, Role.planner)),
):
    try:
        log = create_shift_log(session, payload)
    except MesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(log)
    return _log_read(session, log)


@router.get("/shift-logs", response_model=List[ShiftLogRead])
def list_shift_logs(
    session: Session = Depends(get_session),
    work_centre_id: Optional[int] = None,
    status: Optional[ShiftLogStatus] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    offset: int = 0,
    limit: int = Query(default=100),
):
    offset, limit = page_bounds(offset, limit)
    stmt = select(ShiftLog)
    if work_centre_id is not None:
        stmt = stmt.where(ShiftLog.work_centre_id == work_centre_id)
    if status is not None:
        stmt = stmt.where(ShiftLog.status == status)
    if date_from is not None:
        stmt = stmt.where(ShiftLog.log_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(ShiftLog.log_date <= date_to)
    # Eager-load the downtime collection: without this each log triggers its
    # own lazy load when the read model walks its events.
    logs = session.exec(
        stmt.options(selectinload(ShiftLog.downtime))
        .order_by(ShiftLog.log_date.desc(), ShiftLog.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    ctx = _LogContext(session, logs)
    return [_log_read(session, l, ctx) for l in logs]


@router.get("/shift-logs/{log_id}", response_model=ShiftLogRead)
def get_shift_log(log_id: int, session: Session = Depends(get_session)):
    log = session.get(ShiftLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Shift log not found")
    return _log_read(session, log)


@router.patch("/shift-logs/{log_id}/counts", response_model=ShiftLogRead)
def update_counts(
    log_id: int,
    payload: ShiftCountUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.sewing_supervisor, Role.cutting_supervisor)),
):
    """Record cumulative counts read off the line."""
    log = session.get(ShiftLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Shift log not found")
    if log.status != ShiftLogStatus.open:
        raise HTTPException(status_code=409, detail="Shift log is closed")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None and value < 0:
            raise HTTPException(status_code=422, detail=f"{field} cannot be negative")
        setattr(log, field, value)
    session.add(log)
    session.commit()
    session.refresh(log)
    return _log_read(session, log)


@router.post("/shift-logs/{log_id}/downtime", response_model=ShiftLogRead, status_code=201)
def record_downtime(
    log_id: int,
    payload: DowntimeEventCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.sewing_supervisor, Role.cutting_supervisor)),
):
    log = session.get(ShiftLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Shift log not found")
    try:
        add_downtime(session, log, payload.reason_id, payload.minutes, payload.note, actor)
    except MesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(log)
    return _log_read(session, log)


@router.post("/shift-logs/{log_id}/close", response_model=ShiftLogRead)
def close_log(
    log_id: int,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.sewing_supervisor, Role.cutting_supervisor, Role.planner)),
):
    log = session.get(ShiftLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Shift log not found")
    try:
        close_shift_log(session, log, actor)
    except MesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(log)
    return _log_read(session, log)


# --------------------------------------------------------------------------- #
# OEE reporting
# --------------------------------------------------------------------------- #
@router.get("/oee", response_model=OeeSummaryRead)
def get_oee(
    session: Session = Depends(get_session),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    work_centre_id: Optional[int] = None,
):
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=29))
    if start > end:
        raise HTTPException(status_code=422, detail="date_from cannot follow date_to")
    return OeeSummaryRead(**oee_summary(session, start, end, work_centre_id))


@router.get("/oee/export")
def export_oee(
    session: Session = Depends(get_session),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    work_centre_id: Optional[int] = None,
):
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=29))
    stmt = select(ShiftLog).where(
        ShiftLog.log_date >= start,
        ShiftLog.log_date <= end,
        ShiftLog.status == ShiftLogStatus.closed,
    )
    if work_centre_id is not None:
        stmt = stmt.where(ShiftLog.work_centre_id == work_centre_id)
    rows = []
    for log in session.exec(stmt.order_by(ShiftLog.log_date)).all():
        centre = session.get(WorkCentre, log.work_centre_id)
        rows.append({
            "log_number": log.log_number,
            "log_date": log.log_date,
            "shift": log.shift,
            "work_centre": centre.code if centre else "",
            "planned_minutes": log.planned_minutes,
            "run_minutes": log.run_minutes,
            "planned_downtime": log.planned_downtime_minutes,
            "unplanned_downtime": log.unplanned_downtime_minutes,
            "total_count": log.total_count,
            "good_count": log.good_count,
            "availability_pct": log.availability_pct,
            "performance_pct": log.performance_pct,
            "quality_pct": log.quality_pct,
            "oee_pct": log.oee_pct,
        })
    return csv_download(
        "oee.csv",
        columns=[
            ("log_number", "Log"), ("log_date", "Date"), ("shift", "Shift"),
            ("work_centre", "Work centre"), ("planned_minutes", "Planned min"),
            ("run_minutes", "Run min"), ("planned_downtime", "Planned downtime"),
            ("unplanned_downtime", "Unplanned downtime"),
            ("total_count", "Total"), ("good_count", "Good"),
            ("availability_pct", "Availability %"), ("performance_pct", "Performance %"),
            ("quality_pct", "Quality %"), ("oee_pct", "OEE %"),
        ],
        rows=rows,
    )


@router.get("/andon", response_model=AndonBoardRead)
def andon_board(session: Session = Depends(get_session), as_of: Optional[date] = None):
    """Live line status for the floor display."""
    today = as_of or date.today()
    centres = session.exec(
        select(WorkCentre).where(WorkCentre.active == True).order_by(WorkCentre.code)  # noqa: E712
    ).all()

    # Load everything for the day once, then compute each line in memory. This
    # previously ran a full OEE summary *and* a Pareto per work centre, which is
    # several round trips per tile on a board meant to refresh continuously.
    all_logs = list(
        session.exec(select(ShiftLog).where(ShiftLog.log_date == today)).all()
    )
    logs_by_centre: dict = {}
    for log in all_logs:
        logs_by_centre.setdefault(log.work_centre_id, []).append(log)

    events_by_log: dict = {}
    reason_desc: dict = {}
    if all_logs:
        rows = session.exec(
            select(DowntimeEvent, DowntimeReason)
            .join(DowntimeReason, DowntimeReason.id == DowntimeEvent.reason_id)
            .where(DowntimeEvent.shift_log_id.in_([l.id for l in all_logs]))
        ).all()
        for event, reason in rows:
            events_by_log.setdefault(event.shift_log_id, []).append(event)
            reason_desc[reason.id] = reason.description

    ideal_seconds = resolve_ideal_seconds(session, all_logs)

    lines: List[AndonLine] = []
    for centre in centres:
        logs = logs_by_centre.get(centre.id, [])
        open_logs = [l for l in logs if l.status == ShiftLogStatus.open]
        closed_logs = [l for l in logs if l.status == ShiftLogStatus.closed]

        downtime_minutes = Decimal("0")
        minutes_by_reason: dict = {}
        for log in logs:
            for event in events_by_log.get(log.id, []):
                downtime_minutes += event.minutes
                minutes_by_reason[event.reason_id] = (
                    minutes_by_reason.get(event.reason_id, Decimal("0")) + event.minutes
                )
        top_reason = None
        if minutes_by_reason:
            worst = max(minutes_by_reason.items(), key=lambda kv: kv[1])[0]
            top_reason = reason_desc.get(worst)

        summary = aggregate_factors(closed_logs, ideal_seconds)

        if not logs:
            status = "idle"
        elif open_logs and any(
            events_by_log.get(l.id) and l.total_count == 0 for l in open_logs
        ):
            status = "stopped"
        elif open_logs:
            status = "running"
        else:
            status = "idle"

        lines.append(
            AndonLine(
                work_centre_id=centre.id,
                code=centre.code,
                name=centre.name,
                open_logs=len(open_logs),
                today_oee_pct=summary["oee_pct"],
                today_downtime_minutes=downtime_minutes,
                status=status,
                top_downtime_reason=top_reason,
            )
        )
    return AndonBoardRead(as_of=today, lines=lines)
