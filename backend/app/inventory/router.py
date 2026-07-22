from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.rbac import Role, require_roles
from .models import (
    Grade,
    MovementType,
    Reservation,
    ReservationRead,
    ReserveRequest,
    Roll,
    RollAllocation,
    RollRead,
    RollStatus,
    RollWithBalance,
    StockAdjustmentCreate,
    StockLedgerEntry,
    StockLedgerEntryRead,
)
from .service import (
    StockError,
    balance,
    post_movement,
    query_rolls,
    release_reservation,
    reserve,
    select_rolls,
)

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
    session: Session = Depends(get_session),
):
    stmt = select(StockLedgerEntry).order_by(StockLedgerEntry.id)
    if material_id is not None:
        stmt = stmt.where(StockLedgerEntry.material_id == material_id)
    if roll_id is not None:
        stmt = stmt.where(StockLedgerEntry.roll_id == roll_id)
    return session.exec(stmt).all()


@router.get("/balance")
def get_balance(
    material_id: int,
    warehouse: Optional[str] = None,
    session: Session = Depends(get_session),
):
    on_hand = balance(session, material_id=material_id, warehouse=warehouse)
    return {"material_id": material_id, "warehouse": warehouse, "on_hand": str(on_hand)}


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
    session: Session = Depends(get_session),
):
    return query_rolls(
        session,
        material_id=material_id,
        shade_group=shade_group,
        dye_lot=dye_lot,
        grade=grade,
        status=status,
        min_width_cm=min_width_cm,
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
