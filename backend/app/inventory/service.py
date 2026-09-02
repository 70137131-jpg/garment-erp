"""Stock-ledger posting primitive + balance queries + roll register helpers.

Every module that moves stock (goods receipt, production issue, returns,
dispatch) posts through :func:`post_movement`. That is the *only* way stock
changes, which is what makes "balance = sum of ledger lines" a guarantee rather
than a hope. The function never commits — callers own the transaction so a
movement, a roll update and a domain event all land atomically.
"""

from decimal import Decimal
from typing import Iterable, Optional, Sequence

from sqlalchemy import func
from sqlmodel import Session, select

from ..kernel.types import quantize_money, quantize_qty
from ..kernel.numbering import next_document_number
from ..masters.models import Material, ValuationMethod
from .models import (
    Grade,
    InventoryCostLayer,
    MovementType,
    Reservation,
    ReservationStatus,
    ReserveRequest,
    Roll,
    RollAllocation,
    RollStatus,
    StockLedgerEntry,
    WarehouseOperation,
    WarehouseOperationLine,
    WarehouseOperationType,
)

_GRADE_RANK = {Grade.A: 0, Grade.B: 1, Grade.C: 2}

# Movement types whose sign is fixed by direction; adjustment is caller-signed.
_INBOUND = {
    MovementType.receipt,
    MovementType.return_to_stock,
    MovementType.customer_return,
    MovementType.transfer_in,
    MovementType.split_in,
    MovementType.join_in,
}
_OUTBOUND = {
    MovementType.issue,
    MovementType.transfer_out,
    MovementType.supplier_return,
    MovementType.split_out,
    MovementType.join_out,
}


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
    unit_cost: Optional[Decimal] = None,
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
        # Lock the physical identity before reading its balance.  This is a
        # no-op on SQLite but provides the required exclusion on PostgreSQL.
        if roll_id is not None:
            locked_roll = session.exec(
                select(Roll).where(Roll.id == roll_id).with_for_update()
            ).first()
            if locked_roll is None:
                raise StockError(f"Roll {roll_id} not found")
        available = balance(
            session,
            material_id=material_id,
            roll_id=roll_id,
            warehouse=warehouse,
            location=location,
        )
        if available + signed < 0:
            raise StockError(
                f"Insufficient stock: on hand {available}, tried to remove {abs(signed)}"
            )

    material = session.get(Material, material_id)
    if material is None:
        raise StockError(f"Material {material_id} not found")
    fifo_cost: Optional[Decimal] = None
    if signed > 0:
        resolved_unit_cost = quantize_money(unit_cost if unit_cost is not None else current_unit_cost(
            session, material_id=material_id, warehouse=warehouse, location=location
        ))
    elif signed < 0:
        if unit_cost is not None:
            resolved_unit_cost = quantize_money(unit_cost)
        elif material.valuation_method == ValuationMethod.fifo:
            resolved_unit_cost, fifo_cost = _consume_fifo_layers(
                session, material_id, abs(signed), roll_id, warehouse, location
            )
        else:
            resolved_unit_cost = current_unit_cost(
                session, material_id=material_id, warehouse=warehouse, location=location
            )
    else:
        resolved_unit_cost = Decimal("0")
    extended_cost = quantize_money(-fifo_cost) if fifo_cost is not None else quantize_money(signed * resolved_unit_cost)

    entry = StockLedgerEntry(
        material_id=material_id,
        roll_id=roll_id,
        movement_type=movement_type,
        quantity=signed,
        unit_cost=resolved_unit_cost,
        extended_cost=extended_cost,
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
    if signed > 0:
        session.add(InventoryCostLayer(
            receipt_ledger_entry_id=entry.id,
            material_id=material_id,
            roll_id=roll_id,
            warehouse=warehouse,
            location=location,
            received_qty=signed,
            remaining_qty=signed,
            unit_cost=resolved_unit_cost,
        ))
        session.flush()
    return entry


def stock_value(
    session: Session,
    *,
    material_id: Optional[int] = None,
    warehouse: Optional[str] = None,
    location: Optional[str] = None,
) -> tuple[Decimal, Decimal]:
    """Book value derived from the immutable, signed ledger valuation."""
    stmt = select(func.coalesce(func.sum(StockLedgerEntry.extended_cost), 0))
    if material_id is not None:
        stmt = stmt.where(StockLedgerEntry.material_id == material_id)
    if warehouse is not None:
        stmt = stmt.where(StockLedgerEntry.warehouse == warehouse)
    if location is not None:
        stmt = stmt.where(StockLedgerEntry.location == location)
    return quantize_money(Decimal(session.exec(stmt).one()))


def current_unit_cost(
    session: Session,
    *,
    material_id: int,
    warehouse: Optional[str] = None,
    location: Optional[str] = None,
) -> Decimal:
    qty = balance(session, material_id=material_id, warehouse=warehouse, location=location)
    if qty <= 0:
        return Decimal("0")
    return quantize_money(stock_value(
        session, material_id=material_id, warehouse=warehouse, location=location
    ) / qty)


def _consume_fifo_layers(
    session: Session,
    material_id: int,
    quantity: Decimal,
    roll_id: Optional[int],
    warehouse: str,
    location: Optional[str],
) -> Decimal:
    stmt = select(InventoryCostLayer).where(
        InventoryCostLayer.material_id == material_id,
        InventoryCostLayer.remaining_qty > 0,
        InventoryCostLayer.warehouse == warehouse,
    )
    if roll_id is not None:
        stmt = stmt.where(InventoryCostLayer.roll_id == roll_id)
    if location is None:
        stmt = stmt.where(InventoryCostLayer.location.is_(None))
    else:
        stmt = stmt.where(InventoryCostLayer.location == location)
    layers = session.exec(stmt.order_by(InventoryCostLayer.id).with_for_update()).all()
    remaining = quantity
    cost = Decimal("0")
    for layer in layers:
        taken = min(layer.remaining_qty, remaining)
        cost += taken * layer.unit_cost
        layer.remaining_qty = quantize_qty(layer.remaining_qty - taken)
        session.add(layer)
        remaining -= taken
        if remaining <= 0:
            break
    if remaining > 0:
        raise StockError("Insufficient FIFO cost layers for the requested issue")
    return quantize_money(cost / quantity), quantize_money(cost)


def balance(
    session: Session,
    *,
    material_id: Optional[int] = None,
    roll_id: Optional[int] = None,
    warehouse: Optional[str] = None,
    location: Optional[str] = None,
) -> Decimal:
    """Derived on-hand balance = sum of matching ledger quantities."""
    stmt = select(func.coalesce(func.sum(StockLedgerEntry.quantity), 0))
    if material_id is not None:
        stmt = stmt.where(StockLedgerEntry.material_id == material_id)
    if roll_id is not None:
        stmt = stmt.where(StockLedgerEntry.roll_id == roll_id)
    if warehouse is not None:
        stmt = stmt.where(StockLedgerEntry.warehouse == warehouse)
    if location is not None:
        stmt = stmt.where(StockLedgerEntry.location == location)
    result = session.exec(stmt).one()
    return quantize_qty(Decimal(result))


def begin_operation(
    session: Session,
    operation_type: WarehouseOperationType,
    actor: str,
    *,
    reference: Optional[str] = None,
    reason: Optional[str] = None,
) -> WarehouseOperation:
    """Create a durable warehouse operation header within the caller's transaction."""
    operation = WarehouseOperation(
        operation_number=next_document_number(session, "WAREHOUSE_OPERATION", "WH"),
        operation_type=operation_type,
        reference=reference,
        reason=reason,
        created_by=actor,
    )
    session.add(operation)
    session.flush()
    return operation


def move_stock(
    session: Session,
    *,
    material_id: int,
    quantity: Decimal,
    actor: str,
    from_warehouse: str,
    from_location: Optional[str],
    to_warehouse: str,
    to_location: Optional[str],
    roll_id: Optional[int] = None,
    operation_type: WarehouseOperationType = WarehouseOperationType.transfer,
    reference: Optional[str] = None,
    reason: Optional[str] = None,
) -> WarehouseOperation:
    quantity = quantize_qty(quantity)
    if quantity <= 0:
        raise StockError("Movement quantity must be positive")
    if from_warehouse == to_warehouse and from_location == to_location:
        raise StockError("Source and destination must be different")
    roll = session.get(Roll, roll_id) if roll_id is not None else None
    if roll_id is not None and roll is None:
        raise StockError("Roll not found")
    if roll is not None:
        if roll.material_id != material_id:
            raise StockError("Roll material does not match movement material")
        on_hand = balance(session, roll_id=roll.id, warehouse=from_warehouse, location=from_location)
        if quantity != on_hand:
            raise StockError("A physical roll must be moved in full; split it before a partial move")

    operation = begin_operation(
        session, operation_type, actor, reference=reference, reason=reason
    )
    post_movement(
        session,
        material_id=material_id,
        roll_id=roll_id,
        movement_type=MovementType.transfer_out,
        quantity=quantity,
        warehouse=from_warehouse,
        location=from_location,
        reference_type="warehouse_operation",
        reference_id=operation.id,
        actor=actor,
        note=reason,
    )
    post_movement(
        session,
        material_id=material_id,
        roll_id=roll_id,
        movement_type=MovementType.transfer_in,
        quantity=quantity,
        warehouse=to_warehouse,
        location=to_location,
        reference_type="warehouse_operation",
        reference_id=operation.id,
        actor=actor,
        note=reason,
    )
    if roll is not None:
        roll.warehouse = to_warehouse
        roll.location = to_location
        session.add(roll)
    session.add(
        WarehouseOperationLine(
            operation_id=operation.id,
            material_id=material_id,
            roll_id=roll_id,
            quantity=quantity,
            from_warehouse=from_warehouse,
            from_location=from_location,
            to_warehouse=to_warehouse,
            to_location=to_location,
        )
    )
    session.flush()
    return operation


def return_stock(
    session: Session,
    *,
    material_id: int,
    quantity: Decimal,
    direction: str,
    actor: str,
    warehouse: str,
    location: Optional[str],
    roll_id: Optional[int] = None,
    reference: Optional[str] = None,
    reason: Optional[str] = None,
) -> WarehouseOperation:
    quantity = quantize_qty(quantity)
    if quantity <= 0:
        raise StockError("Return quantity must be positive")
    direction_map = {
        "supplier": (WarehouseOperationType.return_to_supplier, MovementType.supplier_return),
        "customer": (WarehouseOperationType.customer_return, MovementType.customer_return),
        "stock": (WarehouseOperationType.return_to_stock, MovementType.return_to_stock),
    }
    if direction not in direction_map:
        raise StockError("Return direction must be supplier, customer, or stock")
    operation_type, movement_type = direction_map[direction]
    operation = begin_operation(
        session, operation_type, actor, reference=reference, reason=reason
    )
    post_movement(
        session,
        material_id=material_id,
        roll_id=roll_id,
        movement_type=movement_type,
        quantity=quantity,
        warehouse=warehouse,
        location=location,
        reference_type="warehouse_operation",
        reference_id=operation.id,
        actor=actor,
        note=reason,
    )
    session.add(
        WarehouseOperationLine(
            operation_id=operation.id,
            material_id=material_id,
            roll_id=roll_id,
            quantity=quantity,
            from_warehouse=warehouse if direction == "supplier" else None,
            from_location=location if direction == "supplier" else None,
            to_warehouse=warehouse if direction != "supplier" else None,
            to_location=location if direction != "supplier" else None,
        )
    )
    session.flush()
    return operation


def split_roll(
    session: Session,
    source: Roll,
    parts: Iterable,
    actor: str,
    reason: Optional[str] = None,
) -> WarehouseOperation:
    if source.status in (RollStatus.consumed, RollStatus.rejected):
        raise StockError(f"Cannot split a '{source.status.value}' roll")
    if roll_reserved_qty(session, source.id) > 0:
        raise StockError("Release active reservations before splitting a roll")
    on_hand = balance(session, roll_id=source.id, warehouse=source.warehouse, location=source.location)
    parts = list(parts)
    if len(parts) < 2:
        raise StockError("A split requires at least two child rolls")
    quantities = [quantize_qty(part.quantity) for part in parts]
    if any(quantity <= 0 for quantity in quantities):
        raise StockError("Every split quantity must be positive")
    if sum(quantities, Decimal("0")) != on_hand:
        raise StockError(f"Split quantities must equal roll on hand ({on_hand})")

    operation = begin_operation(session, WarehouseOperationType.split_roll, actor, reason=reason)
    post_movement(
        session, material_id=source.material_id, roll_id=source.id,
        movement_type=MovementType.split_out, quantity=on_hand,
        warehouse=source.warehouse, location=source.location,
        reference_type="warehouse_operation", reference_id=operation.id, actor=actor,
    )
    source.status = RollStatus.consumed
    session.add(source)
    for part, quantity in zip(parts, quantities):
        child = Roll(
            roll_number=part.roll_number or next_document_number(session, "ROLL", "ROLL", width=6),
            material_id=source.material_id,
            dye_lot=source.dye_lot,
            shade_code=source.shade_code,
            shade_group=source.shade_group,
            length=quantity,
            width_cm=source.width_cm,
            gsm=source.gsm,
            grade=source.grade,
            warehouse=source.warehouse,
            location=part.location if part.location is not None else source.location,
            status=RollStatus.available,
            supplier_id=source.supplier_id,
            goods_receipt_id=source.goods_receipt_id,
            restricted_customer_id=source.restricted_customer_id,
            parent_roll_id=source.id,
        )
        session.add(child)
        session.flush()
        post_movement(
            session, material_id=child.material_id, roll_id=child.id,
            movement_type=MovementType.split_in, quantity=quantity,
            warehouse=child.warehouse, location=child.location,
            reference_type="warehouse_operation", reference_id=operation.id, actor=actor,
        )
        session.add(WarehouseOperationLine(
            operation_id=operation.id, material_id=source.material_id,
            roll_id=source.id, result_roll_id=child.id, quantity=quantity,
            from_warehouse=source.warehouse, from_location=source.location,
            to_warehouse=child.warehouse, to_location=child.location,
        ))
    session.flush()
    return operation


def join_rolls(
    session: Session,
    rolls: Sequence[Roll],
    actor: str,
    *,
    roll_number: Optional[str] = None,
    reason: Optional[str] = None,
) -> WarehouseOperation:
    if len(rolls) < 2:
        raise StockError("A join requires at least two rolls")
    first = rolls[0]
    identity = (first.material_id, first.dye_lot, first.shade_group, first.grade, first.warehouse, first.location)
    if any((r.material_id, r.dye_lot, r.shade_group, r.grade, r.warehouse, r.location) != identity for r in rolls[1:]):
        raise StockError("Rolls must share material, lot, shade, grade, warehouse, and location")
    if any(r.status != RollStatus.available for r in rolls):
        raise StockError("Only available rolls can be joined")
    if any(roll_reserved_qty(session, r.id) > 0 for r in rolls):
        raise StockError("Release active reservations before joining rolls")
    balances = [balance(session, roll_id=r.id, warehouse=r.warehouse, location=r.location) for r in rolls]
    if any(qty <= 0 for qty in balances):
        raise StockError("Every source roll must have positive stock")

    operation = begin_operation(session, WarehouseOperationType.join_rolls, actor, reason=reason)
    total = quantize_qty(sum(balances, Decimal("0")))
    result = Roll(
        roll_number=roll_number or next_document_number(session, "ROLL", "ROLL", width=6),
        material_id=first.material_id, dye_lot=first.dye_lot,
        shade_code=first.shade_code, shade_group=first.shade_group,
        length=total, width_cm=first.width_cm, gsm=first.gsm, grade=first.grade,
        warehouse=first.warehouse, location=first.location,
        status=RollStatus.available, supplier_id=first.supplier_id,
        restricted_customer_id=first.restricted_customer_id,
    )
    session.add(result)
    session.flush()
    for source, quantity in zip(rolls, balances):
        post_movement(
            session, material_id=source.material_id, roll_id=source.id,
            movement_type=MovementType.join_out, quantity=quantity,
            warehouse=source.warehouse, location=source.location,
            reference_type="warehouse_operation", reference_id=operation.id, actor=actor,
        )
        source.status = RollStatus.consumed
        session.add(source)
        session.add(WarehouseOperationLine(
            operation_id=operation.id, material_id=source.material_id,
            roll_id=source.id, result_roll_id=result.id, quantity=quantity,
            from_warehouse=source.warehouse, from_location=source.location,
            to_warehouse=result.warehouse, to_location=result.location,
        ))
    post_movement(
        session, material_id=result.material_id, roll_id=result.id,
        movement_type=MovementType.join_in, quantity=total,
        warehouse=result.warehouse, location=result.location,
        reference_type="warehouse_operation", reference_id=operation.id, actor=actor,
    )
    session.flush()
    return operation


def query_rolls(
    session: Session,
    *,
    material_id: Optional[int] = None,
    shade_group: Optional[str] = None,
    dye_lot: Optional[str] = None,
    grade: Optional[Grade] = None,
    status: Optional[RollStatus] = None,
    min_width_cm: Optional[Decimal] = None,
    lock: bool = False,
    descending: bool = False,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
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
    stmt = stmt.order_by(Roll.id.desc() if descending else Roll.id)
    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    if lock:
        stmt = stmt.with_for_update()
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


def select_rolls(
    session: Session, req: ReserveRequest, *, lock: bool = False
) -> list[RollAllocation]:
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
        lock=lock,
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
    """Select locked rolls and create active reservations atomically."""
    allocations = select_rolls(session, req, lock=True)
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
