"""Stock-ledger posting primitive + balance queries + roll register helpers.

Every module that moves stock (goods receipt, production issue, returns,
dispatch) posts through :func:`post_movement`. That is the *only* way stock
changes, which is what makes "balance = sum of ledger lines" a guarantee rather
than a hope. The function never commits — callers own the transaction so a
movement, a roll update and a domain event all land atomically.
"""

from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import func
from sqlmodel import Session, select

from ..kernel.types import quantize_qty
from .models import (
    Grade,
    MovementType,
    Reservation,
    ReservationStatus,
    ReserveRequest,
    Roll,
    RollAllocation,
    RollStatus,
    StockLedgerEntry,
)

_GRADE_RANK = {Grade.A: 0, Grade.B: 1, Grade.C: 2}

# Movement types whose sign is fixed by direction; adjustment is caller-signed.
_INBOUND = {MovementType.receipt, MovementType.return_to_stock, MovementType.transfer_in}
_OUTBOUND = {MovementType.issue, MovementType.transfer_out}


class StockError(Exception):
    """Domain error posting stock (negative balance, missing roll…)."""


def post_movement(
    session: Session,
    *,
    material_id: int,
    movement_type: MovementType,
    quantity: Decimal,
    uom: str = "metre",
    roll_id: Optional[int] = None,
    warehouse: str = "MAIN",
    location: Optional[str] = None,
    lot: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[int] = None,
    actor: Optional[str] = None,
    note: Optional[str] = None,
    allow_negative: bool = False,
) -> StockLedgerEntry:
    """Append one immutable ledger line and return it.

    ``quantity`` is given as a positive magnitude for directional movements
    (receipt/issue/…); the sign is applied from ``movement_type``. For
    ``adjustment`` the caller passes a signed quantity. Issuing more than is on
    hand is rejected unless ``allow_negative``.
    """
    quantity = quantize_qty(quantity)

    if movement_type in _INBOUND:
        signed = abs(quantity)
    elif movement_type in _OUTBOUND:
        signed = -abs(quantity)
    else:  # adjustment — caller controls the sign
        signed = quantity

    if signed < 0 and not allow_negative:
        available = balance(session, material_id=material_id, roll_id=roll_id)
        if available + signed < 0:
            raise StockError(
                f"Insufficient stock: on hand {available}, tried to remove {abs(signed)}"
            )

    entry = StockLedgerEntry(
        material_id=material_id,
        roll_id=roll_id,
        movement_type=movement_type,
        quantity=signed,
        uom=uom,
        warehouse=warehouse,
        location=location,
        lot=lot,
        reference_type=reference_type,
        reference_id=reference_id,
        created_by=actor,
        note=note,
    )
    session.add(entry)
    session.flush()
    return entry


def balance(
    session: Session,
    *,
    material_id: Optional[int] = None,
    roll_id: Optional[int] = None,
    warehouse: Optional[str] = None,
) -> Decimal:
    """Derived on-hand balance = sum of matching ledger quantities."""
    stmt = select(func.coalesce(func.sum(StockLedgerEntry.quantity), 0))
    if material_id is not None:
        stmt = stmt.where(StockLedgerEntry.material_id == material_id)
    if roll_id is not None:
        stmt = stmt.where(StockLedgerEntry.roll_id == roll_id)
    if warehouse is not None:
        stmt = stmt.where(StockLedgerEntry.warehouse == warehouse)
    result = session.exec(stmt).one()
    return quantize_qty(Decimal(result))


def query_rolls(
    session: Session,
    *,
    material_id: Optional[int] = None,
    shade_group: Optional[str] = None,
    dye_lot: Optional[str] = None,
    grade: Optional[Grade] = None,
    status: Optional[RollStatus] = None,
    min_width_cm: Optional[Decimal] = None,
) -> Sequence[Roll]:
    """Roll register query (4.3) — the basis of roll-selection logic (4.6)."""
    stmt = select(Roll)
    if material_id is not None:
        stmt = stmt.where(Roll.material_id == material_id)
    if shade_group is not None:
        stmt = stmt.where(Roll.shade_group == shade_group)
    if dye_lot is not None:
        stmt = stmt.where(Roll.dye_lot == dye_lot)
    if grade is not None:
        stmt = stmt.where(Roll.grade == grade)
    if status is not None:
        stmt = stmt.where(Roll.status == status)
    if min_width_cm is not None:
        stmt = stmt.where(Roll.width_cm >= min_width_cm)
    return session.exec(stmt).all()


# --------------------------------------------------------------------------- #
# Reservations (4.4) + roll-selection logic (4.6)
# --------------------------------------------------------------------------- #
def roll_reserved_qty(session: Session, roll_id: int) -> Decimal:
    """Sum of active reservations against a roll."""
    stmt = select(func.coalesce(func.sum(Reservation.reserved_qty), 0)).where(
        Reservation.roll_id == roll_id,
        Reservation.status == ReservationStatus.active,
    )
    return quantize_qty(Decimal(session.exec(stmt).one()))


def roll_available_qty(session: Session, roll: Roll) -> Decimal:
    """Physically on the roll minus what is already reserved on it."""
    return quantize_qty(balance(session, roll_id=roll.id) - roll_reserved_qty(session, roll.id))


def select_rolls(session: Session, req: ReserveRequest) -> list[RollAllocation]:
    """Choose rolls to satisfy a requirement, applying blueprint 4.6 priority:
    shade-group → width/grade → qty fit → oldest → customer restriction.

    ``shade_group`` is treated as a hard filter when given (a single cut must not
    mix shade groups). Customer-restricted rolls are excluded unless they match
    the requesting customer. Raises :class:`StockError` if stock is insufficient.
    """
    required = quantize_qty(req.required_qty)
    if required <= 0:
        raise StockError("Required quantity must be positive")

    candidates = query_rolls(
        session,
        material_id=req.material_id,
        shade_group=req.shade_group,
        grade=req.grade,
        status=RollStatus.available,
        min_width_cm=req.min_width_cm,
    )

    eligible: list[tuple[Roll, Decimal]] = []
    for roll in candidates:
        if roll.restricted_customer_id is not None and (
            req.customer_id is None or roll.restricted_customer_id != req.customer_id
        ):
            continue
        avail = roll_available_qty(session, roll)
        if avail > 0:
            eligible.append((roll, avail))

    # width/grade → oldest ranking (shade group already filtered).
    eligible.sort(key=lambda ra: (_GRADE_RANK.get(ra[0].grade, 9), ra[0].id))

    # qty fit: if a single roll can cover the whole need, take the tightest such
    # roll (least leftover) among the best grade — minimises cuts and waste.
    single = [ra for ra in eligible if ra[1] >= required]
    if single:
        best_grade = min(_GRADE_RANK.get(r.grade, 9) for r, _ in single)
        tight = min(
            (ra for ra in single if _GRADE_RANK.get(ra[0].grade, 9) == best_grade),
            key=lambda ra: (ra[1], ra[0].id),
        )
        return [RollAllocation(roll_id=tight[0].id, roll_number=tight[0].roll_number, qty=required)]

    # Otherwise accumulate greedily in priority order.
    allocations: list[RollAllocation] = []
    remaining = required
    for roll, avail in eligible:
        if remaining <= 0:
            break
        take = min(avail, remaining)
        allocations.append(
            RollAllocation(roll_id=roll.id, roll_number=roll.roll_number, qty=quantize_qty(take))
        )
        remaining -= take

    if remaining > 0:
        raise StockError(
            f"Insufficient available fabric: short by {quantize_qty(remaining)} "
            f"(needed {required})"
        )
    return allocations


def reserve(session: Session, req: ReserveRequest) -> list[Reservation]:
    """Select rolls and create active reservations against them."""
    allocations = select_rolls(session, req)
    reservations: list[Reservation] = []
    for alloc in allocations:
        roll = session.get(Roll, alloc.roll_id)
        reservation = Reservation(
            material_id=req.material_id,
            roll_id=roll.id,
            reserved_qty=alloc.qty,
            status=ReservationStatus.active,
            customer_id=req.customer_id,
            reference_type=req.reference_type,
            reference_id=req.reference_id,
        )
        session.add(reservation)
        # Fully-reserved roll is marked reserved; partial keeps it available for
        # the balance while the active reservation protects the reserved metres.
        if roll_available_qty(session, roll) - alloc.qty <= 0:
            roll.status = RollStatus.reserved
            session.add(roll)
        reservations.append(reservation)
    session.flush()
    return reservations


def release_reservation(session: Session, reservation: Reservation) -> Reservation:
    if reservation.status != ReservationStatus.active:
        raise StockError(f"Reservation is '{reservation.status.value}', not active")
    reservation.status = ReservationStatus.released
    session.add(reservation)
    roll = session.get(Roll, reservation.roll_id) if reservation.roll_id else None
    if roll is not None and roll.status == RollStatus.reserved:
        roll.status = RollStatus.available
    session.flush()
    return reservation
