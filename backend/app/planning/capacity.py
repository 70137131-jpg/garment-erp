"""11.1 / 11.3 — finite capacity and available-to-promise.

Capacity is modelled in minutes because that is the unit the factory already
speaks: SAM is minutes per garment, daily output records operators × minutes.
Anything else would need a conversion nobody trusts.

**Capacity** for a work centre on a day is its nominal
``operators × shift_minutes × shifts × efficiency``, unless a
:class:`CapacityException` overrides it — holidays, overtime, a line down for
maintenance. Weekends are *not* assumed: garment factories run six- and
seven-day weeks routinely, so a non-working day is an explicit exception rather
than a guess baked into the engine.

**Load** comes from bookings. A booking reserves a total number of minutes over
a date range and is spread evenly across the days in that range. Spreading at
read time rather than storing day rows means rescheduling is a single update
and can never leave orphaned days behind.

**ATP** answers the only question sales actually asks: *if I promise this, will
we make it?* It checks both constraints and reports which one binds, because
"no" without a reason cannot be negotiated with a customer.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlmodel import Session, select

from ..kernel.types import quantize_money, quantize_qty
from ..masters.models import Material
from ..styles.models import BomStatus, BomVersion, Style
from ..styles.service import material_requirements
from .models import (
    AtpMaterialLine,
    AtpRequest,
    AtpResponse,
    CapacityBooking,
    CapacityBookingStatus,
    CapacityBucketRead,
    CapacityException,
    CapacityBoardRead,
    WorkCentre,
    WorkCentreLoadRead,
    WorkCentreType,
)
from .mrp import PlanningError, build_buckets, on_hand_by_material

_ZERO = Decimal("0")

# Bookings in these states occupy the line; cancelled ones release it.
_ACTIVE_BOOKINGS = (
    CapacityBookingStatus.planned,
    CapacityBookingStatus.confirmed,
    CapacityBookingStatus.released,
)


# --------------------------------------------------------------------------- #
# Capacity
# --------------------------------------------------------------------------- #
def daily_capacity(
    centre: WorkCentre, day: date, exceptions: Dict[Tuple[int, date], Decimal]
) -> Decimal:
    """Minutes available on one day, honouring any exception for that day."""
    override = exceptions.get((centre.id, day))
    if override is not None:
        return quantize_qty(override)
    return quantize_qty(centre.daily_minutes)


def _exception_map(
    session: Session, centre_ids: List[int], start: date, end: date
) -> Dict[Tuple[int, date], Decimal]:
    if not centre_ids:
        return {}
    rows = session.exec(
        select(CapacityException).where(
            CapacityException.work_centre_id.in_(centre_ids),
            CapacityException.exception_date >= start,
            CapacityException.exception_date <= end,
        )
    ).all()
    return {(r.work_centre_id, r.exception_date): r.available_minutes for r in rows}


def _load_map(
    session: Session, centre_ids: List[int], start: date, end: date
) -> Dict[Tuple[int, date], Decimal]:
    """Spread each booking's minutes evenly over the days it covers."""
    if not centre_ids:
        return {}
    bookings = session.exec(
        select(CapacityBooking).where(
            CapacityBooking.work_centre_id.in_(centre_ids),
            CapacityBooking.status.in_(_ACTIVE_BOOKINGS),
            CapacityBooking.end_date >= start,
            CapacityBooking.start_date <= end,
        )
    ).all()

    load: Dict[Tuple[int, date], Decimal] = {}
    for booking in bookings:
        span_days = (booking.end_date - booking.start_date).days + 1
        if span_days < 1:
            continue
        per_day = booking.minutes / Decimal(span_days)
        for offset in range(span_days):
            day = booking.start_date + timedelta(days=offset)
            if day < start or day > end:
                continue
            key = (booking.work_centre_id, day)
            load[key] = load.get(key, _ZERO) + per_day
    return load


def capacity_board(
    session: Session,
    horizon_start: date,
    horizon_end: date,
    bucket_days: int = 7,
    centre_type: Optional[WorkCentreType] = None,
) -> CapacityBoardRead:
    """Load versus capacity per work centre per bucket."""
    buckets = build_buckets(horizon_start, horizon_end, bucket_days)

    stmt = select(WorkCentre).where(WorkCentre.active == True)  # noqa: E712
    if centre_type is not None:
        stmt = stmt.where(WorkCentre.centre_type == centre_type)
    centres = session.exec(stmt.order_by(WorkCentre.code)).all()
    centre_ids = [c.id for c in centres]

    exceptions = _exception_map(session, centre_ids, horizon_start, horizon_end)
    load = _load_map(session, centre_ids, horizon_start, horizon_end)

    centre_reads: List[WorkCentreLoadRead] = []
    for centre in centres:
        bucket_reads: List[CapacityBucketRead] = []
        total_capacity = _ZERO
        total_load = _ZERO
        overloaded_count = 0

        for bucket_start, bucket_end in buckets:
            capacity = _ZERO
            loaded = _ZERO
            day = bucket_start
            while day <= bucket_end:
                capacity += daily_capacity(centre, day, exceptions)
                loaded += load.get((centre.id, day), _ZERO)
                day += timedelta(days=1)

            capacity = quantize_qty(capacity)
            loaded = quantize_qty(loaded)
            utilisation = (
                quantize_money(loaded / capacity * Decimal("100"))
                if capacity > 0
                else (Decimal("100.00") if loaded > 0 else _ZERO)
            )
            overloaded = loaded > capacity
            if overloaded:
                overloaded_count += 1
            total_capacity += capacity
            total_load += loaded

            bucket_reads.append(
                CapacityBucketRead(
                    bucket_start=bucket_start,
                    bucket_end=bucket_end,
                    capacity_minutes=capacity,
                    loaded_minutes=loaded,
                    available_minutes=quantize_qty(capacity - loaded),
                    utilisation_pct=utilisation,
                    overloaded=overloaded,
                )
            )

        centre_reads.append(
            WorkCentreLoadRead(
                work_centre_id=centre.id,
                code=centre.code,
                name=centre.name,
                centre_type=centre.centre_type,
                buckets=bucket_reads,
                total_capacity=quantize_qty(total_capacity),
                total_load=quantize_qty(total_load),
                utilisation_pct=(
                    quantize_money(total_load / total_capacity * Decimal("100"))
                    if total_capacity > 0
                    else _ZERO
                ),
                overloaded_buckets=overloaded_count,
            )
        )

    return CapacityBoardRead(
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        bucket_days=bucket_days,
        centres=centre_reads,
    )


def free_minutes_by_day(
    session: Session, centre: WorkCentre, start: date, end: date
) -> List[Tuple[date, Decimal]]:
    """Unbooked minutes per day, used by ATP to find the earliest finish."""
    exceptions = _exception_map(session, [centre.id], start, end)
    load = _load_map(session, [centre.id], start, end)
    out: List[Tuple[date, Decimal]] = []
    day = start
    while day <= end:
        capacity = daily_capacity(centre, day, exceptions)
        used = load.get((centre.id, day), _ZERO)
        out.append((day, quantize_qty(max(_ZERO, capacity - used))))
        day += timedelta(days=1)
    return out


# --------------------------------------------------------------------------- #
# Available to promise
# --------------------------------------------------------------------------- #
def available_to_promise(session: Session, payload: AtpRequest) -> AtpResponse:
    """Can we commit this quantity of this style by this date?

    Both constraints are evaluated even when the first one already fails, so
    the answer names every blocker rather than only the first one found.
    """
    if payload.quantity <= 0:
        raise PlanningError("Quantity must be positive")
    style = session.get(Style, payload.style_id)
    if style is None:
        raise PlanningError("Style not found")

    notes: List[str] = []
    today = date.today()

    # ---- material constraint --------------------------------------------- #
    bom = session.exec(
        select(BomVersion)
        .where(
            BomVersion.style_id == style.id,
            BomVersion.status == BomStatus.approved,
        )
        .order_by(BomVersion.version_no.desc())
    ).first()

    material_lines: List[AtpMaterialLine] = []
    material_ready = today
    if bom is None:
        notes.append("Style has no approved BOM; material availability not checked")
    else:
        # Spread the quantity across the BOM's sizes evenly. Without a size
        # breakdown this is the only defensible assumption, and it is stated
        # rather than hidden.
        size_labels = sorted(
            {cell.size_label for line in bom.lines for cell in line.size_consumption}
        )
        if size_labels:
            per_size, remainder = divmod(payload.quantity, len(size_labels))
            size_qty = {label: per_size for label in size_labels}
            if remainder:
                size_qty[size_labels[0]] += remainder
            notes.append(
                f"Quantity spread evenly across {len(size_labels)} sizes for the BOM explosion"
            )
        else:
            size_qty = {}

        on_hand = on_hand_by_material(session)
        for material_id, required in material_requirements(session, bom, size_qty).items():
            material = session.get(Material, material_id)
            available = on_hand.get(int(material_id), _ZERO)
            shortfall = quantize_qty(max(_ZERO, required - available))
            lead_time = material.lead_time_days if material else 0
            earliest = None
            if shortfall > 0:
                earliest = today + timedelta(days=lead_time)
                if earliest > material_ready:
                    material_ready = earliest
            material_lines.append(
                AtpMaterialLine(
                    material_id=int(material_id),
                    material_code=material.code if material else None,
                    material_name=material.name if material else None,
                    required_qty=quantize_qty(required),
                    available_qty=quantize_qty(available),
                    shortfall_qty=shortfall,
                    lead_time_days=lead_time,
                    earliest_available=earliest,
                )
            )

    # ---- capacity constraint --------------------------------------------- #
    sam = style.standard_sam or _ZERO
    required_minutes = quantize_qty(sam * Decimal(payload.quantity))
    capacity_ready: Optional[date] = None
    available_minutes = _ZERO

    centre: Optional[WorkCentre] = None
    if payload.work_centre_id is not None:
        centre = session.get(WorkCentre, payload.work_centre_id)
        if centre is None:
            raise PlanningError("Work centre not found")
    else:
        centre = session.exec(
            select(WorkCentre)
            .where(
                WorkCentre.active == True,  # noqa: E712
                WorkCentre.centre_type == WorkCentreType.sewing,
            )
            .order_by(WorkCentre.code)
        ).first()

    if centre is None:
        notes.append("No sewing work centre configured; capacity not checked")
        capacity_ready = today
    elif required_minutes <= 0:
        notes.append("Style has no standard SAM; capacity not checked")
        capacity_ready = today
    else:
        # Walk forward accumulating free minutes until the work is absorbed.
        # The search window is generous but bounded so an unsatisfiable request
        # terminates instead of scanning forever.
        window_end = max(payload.wanted_date, today) + timedelta(days=365)
        remaining = required_minutes
        for day, free in free_minutes_by_day(session, centre, today, window_end):
            available_minutes += free
            remaining -= free
            if remaining <= 0:
                capacity_ready = day
                break
        available_minutes = quantize_qty(available_minutes)
        if capacity_ready is None:
            notes.append(
                "Capacity cannot absorb this order within a year at current staffing"
            )

    # ---- verdict ---------------------------------------------------------- #
    candidates = [d for d in (material_ready, capacity_ready) if d is not None]
    promise_date = max(candidates) if candidates else None
    can_promise = (
        promise_date is not None
        and capacity_ready is not None
        and promise_date <= payload.wanted_date
    )

    if capacity_ready is None:
        limiting = "capacity"
    elif promise_date == material_ready and material_ready > (capacity_ready or today):
        limiting = "material"
    elif capacity_ready and capacity_ready > material_ready:
        limiting = "capacity"
    else:
        limiting = "none" if can_promise else "material"

    return AtpResponse(
        style_id=style.id,
        quantity=payload.quantity,
        wanted_date=payload.wanted_date,
        can_promise=can_promise,
        promise_date=promise_date,
        limiting_factor=limiting,
        required_minutes=required_minutes,
        available_minutes=available_minutes,
        capacity_ready_date=capacity_ready,
        material_ready_date=material_ready if bom is not None else None,
        materials=material_lines,
        notes=notes,
    )
