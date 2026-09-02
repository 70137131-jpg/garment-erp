from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.rbac import Role, require_roles
from ..kernel.query import csv_download, page_bounds
from .models import (
    CycleCountRequest,
    Grade,
    JoinRollsRequest,
    MoveStockRequest,
    MovementType,
    Reservation,
    ReservationRead,
    RegradeRollRequest,
    ReserveRequest,
    Roll,
    RollAllocation,
    RollRead,
    RollStatus,
    RollWithBalance,
    ReturnStockRequest,
    SplitRollRequest,
    StockAdjustmentCreate,
    StockLedgerEntry,
    StockLedgerEntryRead,
    StockValuationRead,
    WarehouseOperation,
    WarehouseOperationLine,
    WarehouseOperationLineRead,
    WarehouseOperationRead,
    WarehouseOperationType,
)
from .service import (
    StockError,
    balance,
    begin_operation,
    join_rolls,
    move_stock,
    post_movement,
    query_rolls,
    release_reservation,
    reserve,
    return_stock,
    select_rolls,
    stock_value,
    split_roll,
)
from ..kernel.types import quantize_qty
from ..masters.models import Material

router = APIRouter(prefix="/inventory", tags=["inventory"])


# --------------------------------------------------------------------------- #
# Stock ledger (4.1)
# --------------------------------------------------------------------------- #
@router.post("/adjustments", response_model=StockLedgerEntryRead, status_code=201)
def create_adjustment(
    payload: StockAdjustmentCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores)),
):
    """Manual adjustment / opening balance (signed quantity)."""
    try:
        entry = post_movement(
            session,
            material_id=payload.material_id,
            movement_type=MovementType.adjustment,
            quantity=payload.quantity,
            uom=payload.uom,
            warehouse=payload.warehouse,
            location=payload.location,
            lot=payload.lot,
            reference_type="adjustment",
            actor=actor,
            note=payload.note,
        )
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(entry)
    return entry


@router.get("/ledger", response_model=List[StockLedgerEntryRead])
def list_ledger(
    material_id: Optional[int] = None,
    roll_id: Optional[int] = None,
    movement_type: Optional[MovementType] = None,
    offset: int = 0,
    limit: int = 200,
    session: Session = Depends(get_session),
):
    offset, limit = page_bounds(offset, limit)
    stmt = select(StockLedgerEntry).order_by(StockLedgerEntry.id)
    if material_id is not None:
        stmt = stmt.where(StockLedgerEntry.material_id == material_id)
    if roll_id is not None:
        stmt = stmt.where(StockLedgerEntry.roll_id == roll_id)
    if movement_type is not None:
        stmt = stmt.where(StockLedgerEntry.movement_type == movement_type)
    return session.exec(stmt.offset(offset).limit(limit)).all()


@router.get("/ledger/export")
def export_ledger(session: Session = Depends(get_session)):
    entries = session.exec(select(StockLedgerEntry).order_by(StockLedgerEntry.id.desc())).all()
    return csv_download(
        "stock-ledger.csv",
        [
            ("id", "Entry"), ("created_at", "Created At"),
            ("material_id", "Material ID"), ("roll_id", "Roll ID"),
            ("movement_type", "Movement"), ("quantity", "Quantity"),
            ("uom", "UoM"), ("warehouse", "Warehouse"),
            ("location", "Location"), ("reference_type", "Reference Type"),
            ("reference_id", "Reference ID"), ("note", "Note"),
        ],
        [entry.model_dump() for entry in entries],
    )


@router.get("/balance")
def get_balance(
    material_id: int,
    warehouse: Optional[str] = None,
    session: Session = Depends(get_session),
):
    on_hand = balance(session, material_id=material_id, warehouse=warehouse)
    return {"material_id": material_id, "warehouse": warehouse, "on_hand": str(on_hand)}


@router.get("/valuation", response_model=List[StockValuationRead])
def valuation_report(
    material_id: Optional[int] = None,
    warehouse: Optional[str] = None,
    session: Session = Depends(get_session),
):
    materials = (
        [session.get(Material, material_id)]
        if material_id is not None
        else session.exec(select(Material).order_by(Material.id)).all()
    )
    rows = []
    for material in materials:
        if material is None:
            continue
        quantity = balance(session, material_id=material.id, warehouse=warehouse)
        value = stock_value(session, material_id=material.id, warehouse=warehouse)
        rows.append(StockValuationRead(
            material_id=material.id,
            warehouse=warehouse,
            location=None,
            quantity=quantity,
            value=value,
            unit_cost=(value / quantity).quantize(Decimal("0.01")) if quantity else Decimal("0"),
        ))
    return rows


# --------------------------------------------------------------------------- #
# Roll register (4.2 / 4.3)
# --------------------------------------------------------------------------- #
@router.get("/rolls", response_model=List[RollRead])
def list_rolls(
    material_id: Optional[int] = None,
    shade_group: Optional[str] = None,
    dye_lot: Optional[str] = None,
    grade: Optional[Grade] = None,
    status: Optional[RollStatus] = None,
    min_width_cm: Optional[Decimal] = None,
    offset: int = 0,
    limit: int = 200,
    session: Session = Depends(get_session),
):
    offset, limit = page_bounds(offset, limit)
    rows = query_rolls(
        session,
        material_id=material_id,
        shade_group=shade_group,
        dye_lot=dye_lot,
        grade=grade,
        status=status,
        min_width_cm=min_width_cm,
        descending=True,
        offset=offset,
        limit=limit,
    )
    return rows


@router.get("/rolls/export")
def export_rolls(session: Session = Depends(get_session)):
    rolls = session.exec(select(Roll).order_by(Roll.id.desc())).all()
    return csv_download(
        "roll-register.csv",
        [
            ("roll_number", "Roll"), ("material_id", "Material ID"),
            ("status", "Status"), ("grade", "Grade"), ("length", "Original Length"),
            ("dye_lot", "Dye Lot"), ("shade_group", "Shade Group"),
            ("warehouse", "Warehouse"), ("location", "Location"),
        ],
        [roll.model_dump() for roll in rolls],
    )


@router.get("/rolls/{roll_id}", response_model=RollWithBalance)
def get_roll(roll_id: int, session: Session = Depends(get_session)):
    roll = session.get(Roll, roll_id)
    if roll is None:
        raise HTTPException(status_code=404, detail="Roll not found")
    on_hand = balance(session, roll_id=roll_id)
    data = RollWithBalance(**roll.model_dump(), on_hand=on_hand)
    return data


# --------------------------------------------------------------------------- #
# Reservations + roll selection (4.4 / 4.6)
# --------------------------------------------------------------------------- #
@router.post("/roll-selection", response_model=List[RollAllocation])
def preview_roll_selection(req: ReserveRequest, session: Session = Depends(get_session)):
    """Dry-run: which rolls would satisfy this requirement (no reservation made)."""
    try:
        return select_rolls(session, req)
    except StockError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reservations", response_model=List[ReservationRead], status_code=201)
def create_reservation(
    req: ReserveRequest,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.stores, Role.planner)),
):
    try:
        reservations = reserve(session, req)
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    for r in reservations:
        session.refresh(r)
    return reservations


@router.get("/reservations", response_model=List[ReservationRead])
def list_reservations(
    reference_type: Optional[str] = None,
    reference_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    stmt = select(Reservation).order_by(Reservation.id)
    if reference_type is not None:
        stmt = stmt.where(Reservation.reference_type == reference_type)
    if reference_id is not None:
        stmt = stmt.where(Reservation.reference_id == reference_id)
    return session.exec(stmt).all()


@router.post("/reservations/{reservation_id}/release", response_model=ReservationRead)
def release(
    reservation_id: int,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.stores, Role.planner)),
):
    reservation = session.get(Reservation, reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    try:
        release_reservation(session, reservation)
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(reservation)
    return reservation


# --------------------------------------------------------------------------- #
# Warehouse execution: transfers, returns, put-away, roll transforms, counts.
# --------------------------------------------------------------------------- #
def _operation_read(session: Session, operation: WarehouseOperation) -> WarehouseOperationRead:
    lines = session.exec(
        select(WarehouseOperationLine)
        .where(WarehouseOperationLine.operation_id == operation.id)
        .order_by(WarehouseOperationLine.id)
    ).all()
    return WarehouseOperationRead(
        id=operation.id,
        operation_number=operation.operation_number,
        operation_type=operation.operation_type,
        reference=operation.reference,
        reason=operation.reason,
        created_at=operation.created_at,
        created_by=operation.created_by,
        lines=[WarehouseOperationLineRead.model_validate(line) for line in lines],
    )


@router.get("/operations", response_model=List[WarehouseOperationRead])
def list_operations(
    operation_type: Optional[WarehouseOperationType] = None,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    if offset < 0 or limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="Invalid pagination bounds")
    stmt = select(WarehouseOperation).order_by(WarehouseOperation.id.desc())
    if operation_type is not None:
        stmt = stmt.where(WarehouseOperation.operation_type == operation_type)
    operations = session.exec(stmt.offset(offset).limit(limit)).all()
    return [_operation_read(session, operation) for operation in operations]


@router.get("/operations/{operation_id}", response_model=WarehouseOperationRead)
def get_operation(operation_id: int, session: Session = Depends(get_session)):
    operation = session.get(WarehouseOperation, operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Warehouse operation not found")
    return _operation_read(session, operation)


@router.post("/transfers", response_model=WarehouseOperationRead, status_code=201)
def create_transfer(
    payload: MoveStockRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores)),
):
    try:
        operation = move_stock(
            session,
            material_id=payload.material_id,
            quantity=payload.quantity,
            roll_id=payload.roll_id,
            from_warehouse=payload.from_warehouse,
            from_location=payload.from_location,
            to_warehouse=payload.to_warehouse,
            to_location=payload.to_location,
            operation_type=WarehouseOperationType.transfer,
            reference=payload.reference,
            reason=payload.reason,
            actor=actor,
        )
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _operation_read(session, operation)


@router.post("/put-away", response_model=WarehouseOperationRead, status_code=201)
def put_away(
    payload: MoveStockRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores)),
):
    if payload.from_warehouse != payload.to_warehouse:
        raise HTTPException(status_code=422, detail="Put-away must remain in one warehouse")
    try:
        operation = move_stock(
            session,
            material_id=payload.material_id,
            quantity=payload.quantity,
            roll_id=payload.roll_id,
            from_warehouse=payload.from_warehouse,
            from_location=payload.from_location,
            to_warehouse=payload.to_warehouse,
            to_location=payload.to_location,
            operation_type=WarehouseOperationType.put_away,
            reference=payload.reference,
            reason=payload.reason,
            actor=actor,
        )
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _operation_read(session, operation)


@router.post("/returns", response_model=WarehouseOperationRead, status_code=201)
def create_return(
    payload: ReturnStockRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores)),
):
    try:
        operation = return_stock(
            session,
            material_id=payload.material_id,
            quantity=payload.quantity,
            direction=payload.direction,
            roll_id=payload.roll_id,
            warehouse=payload.warehouse,
            location=payload.location,
            reference=payload.reference,
            reason=payload.reason,
            actor=actor,
        )
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _operation_read(session, operation)


@router.post("/rolls/{roll_id}/split", response_model=WarehouseOperationRead, status_code=201)
def split_existing_roll(
    roll_id: int,
    payload: SplitRollRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores)),
):
    roll = session.get(Roll, roll_id)
    if roll is None:
        raise HTTPException(status_code=404, detail="Roll not found")
    try:
        operation = split_roll(session, roll, payload.parts, actor, payload.reason)
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _operation_read(session, operation)


@router.post("/rolls/join", response_model=WarehouseOperationRead, status_code=201)
def join_existing_rolls(
    payload: JoinRollsRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores)),
):
    if len(set(payload.roll_ids)) != len(payload.roll_ids):
        raise HTTPException(status_code=422, detail="Roll IDs must be unique")
    rolls = [session.get(Roll, roll_id) for roll_id in payload.roll_ids]
    if any(roll is None for roll in rolls):
        raise HTTPException(status_code=404, detail="One or more rolls were not found")
    try:
        operation = join_rolls(
            session, rolls, actor, roll_number=payload.roll_number, reason=payload.reason
        )
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _operation_read(session, operation)


@router.post("/rolls/{roll_id}/regrade", response_model=WarehouseOperationRead, status_code=201)
def regrade_roll(
    roll_id: int,
    payload: RegradeRollRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores, Role.quality_inspector)),
):
    roll = session.get(Roll, roll_id)
    if roll is None:
        raise HTTPException(status_code=404, detail="Roll not found")
    if roll.status in (RollStatus.consumed, RollStatus.rejected):
        raise HTTPException(status_code=409, detail="Consumed or rejected rolls cannot be regraded")
    if roll.grade == payload.grade:
        raise HTTPException(status_code=422, detail="New grade must differ from current grade")
    old_grade = roll.grade
    operation = begin_operation(
        session, WarehouseOperationType.regrade, actor, reason=payload.reason
    )
    session.add(WarehouseOperationLine(
        operation_id=operation.id,
        material_id=roll.material_id,
        roll_id=roll.id,
        quantity=balance(session, roll_id=roll.id),
        from_warehouse=roll.warehouse,
        from_location=roll.location,
        old_grade=old_grade,
        new_grade=payload.grade,
    ))
    roll.grade = payload.grade
    session.add(roll)
    session.commit()
    return _operation_read(session, operation)


@router.post("/cycle-counts", response_model=WarehouseOperationRead, status_code=201)
def post_cycle_count(
    payload: CycleCountRequest,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores)),
):
    if not payload.lines:
        raise HTTPException(status_code=422, detail="Cycle count needs at least one line")
    operation = begin_operation(
        session,
        WarehouseOperationType.cycle_count,
        actor,
        reference=payload.reference,
        reason=payload.reason,
    )
    try:
        for counted in payload.lines:
            if counted.counted_qty < 0:
                raise StockError("Counted quantity cannot be negative")
            system_qty = balance(
                session,
                material_id=counted.material_id,
                roll_id=counted.roll_id,
                warehouse=counted.warehouse,
                location=counted.location,
            )
            variance = quantize_qty(counted.counted_qty - system_qty)
            if variance:
                post_movement(
                    session,
                    material_id=counted.material_id,
                    roll_id=counted.roll_id,
                    movement_type=MovementType.cycle_count,
                    quantity=variance,
                    warehouse=counted.warehouse,
                    location=counted.location,
                    reference_type="warehouse_operation",
                    reference_id=operation.id,
                    actor=actor,
                    note=payload.reason,
                )
            session.add(WarehouseOperationLine(
                operation_id=operation.id,
                material_id=counted.material_id,
                roll_id=counted.roll_id,
                quantity=abs(variance),
                from_warehouse=counted.warehouse,
                from_location=counted.location,
                system_qty=system_qty,
                counted_qty=quantize_qty(counted.counted_qty),
                variance_qty=variance,
            ))
    except StockError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _operation_read(session, operation)
