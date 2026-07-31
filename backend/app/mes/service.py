"""12.1 — OEE arithmetic and shift-log lifecycle.

Every guard in here exists because the corresponding number is meaningless
without it:

* **Zero planned production time** — a shift entirely consumed by planned
  downtime has no denominator. OEE is reported as zero rather than as a
  division error, and availability is not silently treated as 100%.
* **Performance above 100%** — means the ideal cycle time is wrong, not that
  the line beat physics. The factor is capped at 100% and the log is flagged
  ``performance_capped`` so the master data gets fixed instead of the number
  being quietly believed.
* **Counts that do not add up** — ``good + reject`` must equal ``total``, or
  quality is computed against a denominator nobody agreed on.
* **Downtime exceeding the shift** — recorded stoppages longer than the shift
  itself are rejected; the usual cause is double-entry, and letting it through
  produces a negative run time and a nonsense OEE.
"""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlmodel import Session, select

from ..kernel.audit import utcnow
from ..kernel.numbering import next_document_number
from ..kernel.types import quantize_qty
from ..planning.models import WorkCentre
from .models import (
    DowntimeEvent,
    DowntimeParetoLine,
    DowntimeReason,
    Machine,
    ShiftLog,
    ShiftLogCreate,
    ShiftLogStatus,
)

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class MesError(Exception):
    """Domain error recording or closing shift execution."""


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        return _ZERO
    return (numerator / denominator) * _HUNDRED


def _round_pct(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))


# --------------------------------------------------------------------------- #
# Shift logs
# --------------------------------------------------------------------------- #
def create_shift_log(session: Session, payload: ShiftLogCreate) -> ShiftLog:
    centre = session.get(WorkCentre, payload.work_centre_id)
    if centre is None:
        raise MesError("Work centre not found")
    if payload.machine_id is not None:
        machine = session.get(Machine, payload.machine_id)
        if machine is None:
            raise MesError("Machine not found")
        if machine.work_centre_id != centre.id:
            raise MesError("Machine does not belong to this work centre")

    existing = session.exec(
        select(ShiftLog).where(
            ShiftLog.work_centre_id == payload.work_centre_id,
            ShiftLog.log_date == payload.log_date,
            ShiftLog.shift == payload.shift,
            ShiftLog.machine_id == payload.machine_id,
        )
    ).first()
    if existing is not None:
        raise MesError(
            "A shift log already exists for this work centre, machine, date and shift"
        )

    planned = payload.planned_minutes
    if planned is None:
        # Fall back to the centre's own nominal capacity for the day, scaled by
        # the operators actually present when that is known.
        planned = centre.daily_minutes
        if payload.operators and centre.operators:
            planned = planned * Decimal(payload.operators) / Decimal(centre.operators)
    planned = quantize_qty(planned)
    if planned <= 0:
        raise MesError("Planned minutes must be positive")

    log = ShiftLog(
        log_number=next_document_number(session, "SHIFT_LOG", "SHIFT"),
        work_centre_id=payload.work_centre_id,
        machine_id=payload.machine_id,
        sewing_order_id=payload.sewing_order_id,
        log_date=payload.log_date,
        shift=payload.shift,
        operators=payload.operators,
        planned_minutes=planned,
        ideal_cycle_seconds=payload.ideal_cycle_seconds,
        notes=payload.notes,
        status=ShiftLogStatus.open,
    )
    session.add(log)
    session.flush()
    return log


def add_downtime(
    session: Session, log: ShiftLog, reason_id: int, minutes: Decimal, note: Optional[str], actor: str
) -> DowntimeEvent:
    if log.status != ShiftLogStatus.open:
        raise MesError("Cannot record downtime against a closed shift log")
    if minutes <= 0:
        raise MesError("Downtime minutes must be positive")
    reason = session.get(DowntimeReason, reason_id)
    if reason is None:
        raise MesError("Downtime reason not found")

    recorded = session.exec(
        select(func.coalesce(func.sum(DowntimeEvent.minutes), 0)).where(
            DowntimeEvent.shift_log_id == log.id
        )
    ).one()
    if quantize_qty(Decimal(recorded) + minutes) > log.planned_minutes:
        raise MesError(
            "Recorded downtime would exceed the shift's planned minutes "
            f"({log.planned_minutes})"
        )

    event = DowntimeEvent(
        shift_log_id=log.id,
        reason_id=reason_id,
        minutes=quantize_qty(minutes),
        note=note,
        recorded_by=actor,
    )
    session.add(event)
    session.flush()
    return event


def split_downtime(session: Session, log: ShiftLog) -> Tuple[Decimal, Decimal]:
    """``(planned_downtime, unplanned_downtime)`` in minutes."""
    rows = session.exec(
        select(DowntimeReason.planned, func.coalesce(func.sum(DowntimeEvent.minutes), 0))
        .join(DowntimeEvent, DowntimeEvent.reason_id == DowntimeReason.id)
        .where(DowntimeEvent.shift_log_id == log.id)
        .group_by(DowntimeReason.planned)
    ).all()
    planned = _ZERO
    unplanned = _ZERO
    for is_planned, minutes in rows:
        if is_planned:
            planned += Decimal(minutes)
        else:
            unplanned += Decimal(minutes)
    return quantize_qty(planned), quantize_qty(unplanned)


def compute_oee(
    session: Session, log: ShiftLog
) -> Dict[str, Decimal | bool]:
    """The three factors and their product for one shift log."""
    planned_downtime, unplanned_downtime = split_downtime(session, log)

    planned_production_time = log.planned_minutes - planned_downtime
    if planned_production_time < 0:
        planned_production_time = _ZERO
    run_minutes = planned_production_time - unplanned_downtime
    if run_minutes < 0:
        run_minutes = _ZERO

    availability = _pct(run_minutes, planned_production_time)

    ideal_seconds = log.ideal_cycle_seconds
    if ideal_seconds is None and log.machine_id is not None:
        machine = session.get(Machine, log.machine_id)
        ideal_seconds = machine.ideal_cycle_seconds if machine else None

    performance = _ZERO
    capped = False
    if ideal_seconds and ideal_seconds > 0 and run_minutes > 0:
        ideal_minutes = (Decimal(ideal_seconds) / Decimal("60")) * Decimal(log.total_count)
        performance = _pct(ideal_minutes, run_minutes)
        if performance > _HUNDRED:
            # The line cannot outrun its own rated cycle: the master data is
            # wrong. Cap and flag rather than publish an OEE above reality.
            performance = _HUNDRED
            capped = True

    quality = _pct(Decimal(log.good_count), Decimal(log.total_count))

    oee = (availability / _HUNDRED) * (performance / _HUNDRED) * (quality / _HUNDRED) * _HUNDRED

    return {
        "planned_downtime_minutes": planned_downtime,
        "unplanned_downtime_minutes": unplanned_downtime,
        "run_minutes": quantize_qty(run_minutes),
        "availability_pct": _round_pct(availability),
        "performance_pct": _round_pct(performance),
        "quality_pct": _round_pct(quality),
        "oee_pct": _round_pct(oee),
        "performance_capped": capped,
    }


def close_shift_log(session: Session, log: ShiftLog, actor: str) -> ShiftLog:
    """Freeze the counts and store the computed OEE."""
    if log.status == ShiftLogStatus.closed:
        raise MesError("This shift log is already closed")
    if log.total_count < 0 or log.good_count < 0 or log.reject_count < 0:
        raise MesError("Counts cannot be negative")
    if log.good_count + log.reject_count != log.total_count:
        raise MesError(
            "Good and reject counts must sum to the total count "
            f"({log.good_count} + {log.reject_count} ≠ {log.total_count})"
        )

    for field, value in compute_oee(session, log).items():
        setattr(log, field, value)
    log.status = ShiftLogStatus.closed
    log.closed_at = utcnow().isoformat()
    log.closed_by = actor
    session.add(log)
    session.flush()
    return log


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #
def downtime_pareto(
    session: Session,
    date_from: date,
    date_to: date,
    work_centre_id: Optional[int] = None,
) -> List[DowntimeParetoLine]:
    """Stoppage minutes by reason, largest first, with a cumulative share."""
    stmt = (
        select(
            DowntimeReason.id,
            DowntimeReason.code,
            DowntimeReason.description,
            DowntimeReason.category,
            DowntimeReason.planned,
            func.coalesce(func.sum(DowntimeEvent.minutes), 0),
            func.count(DowntimeEvent.id),
        )
        .join(DowntimeEvent, DowntimeEvent.reason_id == DowntimeReason.id)
        .join(ShiftLog, ShiftLog.id == DowntimeEvent.shift_log_id)
        .where(ShiftLog.log_date >= date_from, ShiftLog.log_date <= date_to)
        .group_by(
            DowntimeReason.id,
            DowntimeReason.code,
            DowntimeReason.description,
            DowntimeReason.category,
            DowntimeReason.planned,
        )
    )
    if work_centre_id is not None:
        stmt = stmt.where(ShiftLog.work_centre_id == work_centre_id)

    rows = sorted(session.exec(stmt).all(), key=lambda r: Decimal(r[5]), reverse=True)
    total = sum((Decimal(r[5]) for r in rows), _ZERO)

    out: List[DowntimeParetoLine] = []
    cumulative = _ZERO
    for reason_id, code, description, category, planned, minutes, events in rows:
        minutes = quantize_qty(Decimal(minutes))
        share = _pct(minutes, total)
        cumulative += share
        out.append(
            DowntimeParetoLine(
                reason_id=reason_id,
                reason_code=code,
                description=description,
                category=category,
                planned=bool(planned),
                minutes=minutes,
                events=int(events),
                share_pct=_round_pct(share),
                cumulative_pct=_round_pct(min(cumulative, _HUNDRED)),
            )
        )
    return out


def aggregate_factors(
    logs: List[ShiftLog], ideal_seconds_by_log: Dict[int, Optional[Decimal]]
) -> Dict:
    """Roll closed logs up into the three factors — pure, no database access.

    Kept separate so the andon board can reuse it over logs it has already
    loaded instead of re-querying per work centre.

    The factors are recomputed from summed minutes and counts rather than
    averaged from each shift's percentages: averaging ratios would weight a
    two-hour shift the same as a twelve-hour one.
    """
    planned_minutes = sum((l.planned_minutes for l in logs), _ZERO)
    planned_downtime = sum((l.planned_downtime_minutes for l in logs), _ZERO)
    unplanned_downtime = sum((l.unplanned_downtime_minutes for l in logs), _ZERO)
    run_minutes = sum((l.run_minutes for l in logs), _ZERO)
    total_count = sum(l.total_count for l in logs)
    good_count = sum(l.good_count for l in logs)
    reject_count = sum(l.reject_count for l in logs)

    planned_production_time = planned_minutes - planned_downtime
    availability = _pct(run_minutes, planned_production_time)
    quality = _pct(Decimal(good_count), Decimal(total_count))

    # Performance uses each log's own ideal cycle — it can legitimately differ
    # per style, so a single blended rate would be wrong.
    ideal_minutes = _ZERO
    for log in logs:
        ideal_seconds = ideal_seconds_by_log.get(log.id)
        if ideal_seconds:
            ideal_minutes += (Decimal(ideal_seconds) / Decimal("60")) * Decimal(log.total_count)
    performance = _pct(ideal_minutes, run_minutes)
    if performance > _HUNDRED:
        performance = _HUNDRED

    oee = (availability / _HUNDRED) * (performance / _HUNDRED) * (quality / _HUNDRED) * _HUNDRED

    return {
        "shifts": len(logs),
        "planned_minutes": quantize_qty(planned_minutes),
        "planned_downtime_minutes": quantize_qty(planned_downtime),
        "unplanned_downtime_minutes": quantize_qty(unplanned_downtime),
        "run_minutes": quantize_qty(run_minutes),
        "total_count": total_count,
        "good_count": good_count,
        "reject_count": reject_count,
        "availability_pct": _round_pct(availability),
        "performance_pct": _round_pct(performance),
        "quality_pct": _round_pct(quality),
        "oee_pct": _round_pct(oee),
    }


def resolve_ideal_seconds(
    session: Session, logs: List[ShiftLog]
) -> Dict[int, Optional[Decimal]]:
    """``{log_id: ideal_cycle_seconds}``, batching the machine lookup."""
    machine_ids = {l.machine_id for l in logs if l.machine_id and l.ideal_cycle_seconds is None}
    machines: Dict[int, Machine] = {}
    if machine_ids:
        machines = {
            m.id: m
            for m in session.exec(select(Machine).where(Machine.id.in_(machine_ids))).all()
        }
    out: Dict[int, Optional[Decimal]] = {}
    for log in logs:
        if log.ideal_cycle_seconds is not None:
            out[log.id] = log.ideal_cycle_seconds
        elif log.machine_id and log.machine_id in machines:
            out[log.id] = machines[log.machine_id].ideal_cycle_seconds
        else:
            out[log.id] = None
    return out


def oee_summary(
    session: Session,
    date_from: date,
    date_to: date,
    work_centre_id: Optional[int] = None,
) -> Dict:
    """Aggregate OEE across closed shift logs.

    The factors are recomputed from summed minutes and counts rather than
    averaged from each shift's percentages — averaging ratios would weight a
    two-hour shift the same as a twelve-hour one.
    """
    stmt = select(ShiftLog).where(
        ShiftLog.log_date >= date_from,
        ShiftLog.log_date <= date_to,
        ShiftLog.status == ShiftLogStatus.closed,
    )
    if work_centre_id is not None:
        stmt = stmt.where(ShiftLog.work_centre_id == work_centre_id)
    logs = list(session.exec(stmt).all())
    factors = aggregate_factors(logs, resolve_ideal_seconds(session, logs))
    return {
        "date_from": date_from,
        "date_to": date_to,
        "work_centre_id": work_centre_id,
        **factors,
        "pareto": downtime_pareto(session, date_from, date_to, work_centre_id),
    }
