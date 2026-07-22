"""Production services: fabric calc, roll issue (ledger consumption), sewing
efficiency, and subcontract balances."""

from decimal import Decimal
from typing import Dict, List

from sqlmodel import Session, select

from ..events import ProductionConfirmed
from ..inventory.models import MovementType, Reservation, ReservationStatus, Roll, RollStatus
from ..inventory.service import (
    balance,
    post_movement,
    roll_available_qty,
)
from ..kernel.events import emit
from ..kernel.numbering import next_document_number
from ..kernel.state_machine import StateMachine
from ..kernel.types import quantize_qty, quantize_money
from ..masters.models import Material, MaterialType, SizeRangeItem
from ..styles.models import BomStatus, BomVersion, Style
from ..styles.service import material_requirements
from .models import (
    CutIssue,
    CutOrder,
    CutOrderCreate,
    CutOrderSize,
    CutOrderStatus,
    SewingDailyOutput,
    SewingOrder,
    SewingOrderStatus,
    SubcontractOrder,
    SubcontractStatus,
)

cut_order_state_machine = StateMachine(
    transitions={
        CutOrderStatus.planned.value: {
            CutOrderStatus.fabric_reserved.value,
            CutOrderStatus.in_cutting.value,
            CutOrderStatus.cancelled.value,
        },
        CutOrderStatus.fabric_reserved.value: {
            CutOrderStatus.in_cutting.value,
            CutOrderStatus.cancelled.value,
        },
        CutOrderStatus.in_cutting.value: {
            CutOrderStatus.completed.value,
            CutOrderStatus.cancelled.value,
        },
        CutOrderStatus.completed.value: set(),
        CutOrderStatus.cancelled.value: set(),
    },
    initial=CutOrderStatus.planned.value,
)


class ProductionError(Exception):
    """Domain error in a production operation."""


def _size_map(cut_order: CutOrder) -> Dict[str, int]:
    return {s.size_label: s.planned_qty for s in cut_order.sizes}


def create_cut_order(session: Session, payload: CutOrderCreate) -> CutOrder:
    """Create a cut order and compute its fabric requirement from the size-BOM."""
    style = session.get(Style, payload.style_id)
    if style is None:
        raise ProductionError("Style not found")

    # Resolve the BOM version: explicit, else the style's current approved one.
    if payload.bom_version_id is not None:
        version = session.get(BomVersion, payload.bom_version_id)
        if version is None or version.style_id != style.id:
            raise ProductionError("BOM version does not belong to this style")
    else:
        version = session.exec(
            select(BomVersion).where(
                BomVersion.style_id == style.id, BomVersion.status == BomStatus.approved
            )
        ).first()
        if version is None:
            raise ProductionError("Style has no approved BOM version")
    if version.status not in (BomStatus.approved, BomStatus.superseded):
        raise ProductionError("Cut orders must reference an approved BOM version")

    # Identify the shell fabric line.
    fabric_material_id = None
    for line in version.lines:
        material = session.get(Material, line.material_id)
        if material and material.material_type == MaterialType.fabric:
            fabric_material_id = material.id
            break
    if fabric_material_id is None:
        raise ProductionError("BOM has no fabric line to cut from")

    # Validate sizes against the style's range and compute requirement.
    valid_labels = {
        i.label
        for i in session.exec(
            select(SizeRangeItem).where(SizeRangeItem.size_range_id == style.size_range_id)
        ).all()
    }
    size_qty: Dict[str, int] = {}
    for s in payload.sizes:
        if s.size_label not in valid_labels:
            raise ProductionError(f"Size '{s.size_label}' is not in the style's range")
        if s.planned_qty < 0:
            raise ProductionError("Planned quantity cannot be negative")
        size_qty[s.size_label] = s.planned_qty

    reqs = material_requirements(session, version, size_qty)
    fabric_required = reqs.get(fabric_material_id, Decimal("0"))

    number = next_document_number(session, "CUT_ORDER", "CO")
    cut_order = CutOrder(
        order_number=number,
        sales_order_id=payload.sales_order_id,
        style_id=style.id,
        colour_id=payload.colour_id,
        bom_version_id=version.id,
        fabric_material_id=fabric_material_id,
        marker_efficiency=payload.marker_efficiency,
        fabric_required=fabric_required,
        status=CutOrderStatus.planned,
    )
    session.add(cut_order)
    session.flush()

    for s in payload.sizes:
        item_pos = next(
            (
                i.position
                for i in session.exec(
                    select(SizeRangeItem).where(
                        SizeRangeItem.size_range_id == style.size_range_id,
                        SizeRangeItem.label == s.size_label,
                    )
                ).all()
            ),
            0,
        )
        session.add(
            CutOrderSize(
                cut_order_id=cut_order.id,
                size_label=s.size_label,
                position=item_pos,
                planned_qty=s.planned_qty,
            )
        )
    session.flush()
    session.refresh(cut_order)
    return cut_order


def issue_fabric(
    session: Session, cut_order: CutOrder, allocations: List, actor: str
) -> CutOrder:
    """Issue rolls to cutting: post ledger consumption and consume reservations.

    ``allocations`` is a list of (roll_id, quantity). If empty, the cut order's
    active reservations are consumed in full. Issuing advances the order to
    in_cutting.
    """
    if cut_order.status not in (
        CutOrderStatus.planned,
        CutOrderStatus.fabric_reserved,
        CutOrderStatus.in_cutting,
    ):
        raise ProductionError(
            f"Cannot issue fabric to a '{cut_order.status.value}' cut order"
        )

    pairs: List[tuple[int, Decimal]] = []
    if allocations:
        pairs = [(a.roll_id, quantize_qty(a.quantity)) for a in allocations]
    else:
        reservations = session.exec(
            select(Reservation).where(
                Reservation.reference_type == "cut_order",
                Reservation.reference_id == cut_order.id,
                Reservation.status == ReservationStatus.active,
            )
        ).all()
        if not reservations:
            raise ProductionError(
                "No allocations given and no active reservations to issue from"
            )
        pairs = [(r.roll_id, r.reserved_qty) for r in reservations]

    issued_total = Decimal("0")
    for roll_id, qty in pairs:
        roll = session.get(Roll, roll_id)
        if roll is None:
            raise ProductionError(f"Roll {roll_id} not found")
        if roll.material_id != cut_order.fabric_material_id:
            raise ProductionError(
                f"Roll {roll.roll_number} is a different material to the cut order"
            )
        if qty <= 0:
            raise ProductionError("Issue quantity must be positive")

        # Consume active reservations on this roll for this cut order first, so
        # available-qty checks stay honest.
        remaining_res = qty
        reservations = session.exec(
            select(Reservation).where(
                Reservation.roll_id == roll_id,
                Reservation.reference_type == "cut_order",
                Reservation.reference_id == cut_order.id,
                Reservation.status == ReservationStatus.active,
            )
        ).all()
        for res in reservations:
            if remaining_res <= 0:
                break
            res.status = ReservationStatus.consumed
            session.add(res)
            remaining_res -= res.reserved_qty

        on_hand = balance(session, roll_id=roll_id)
        if on_hand < qty:
            raise ProductionError(
                f"Roll {roll.roll_number} has only {on_hand} on hand, cannot issue {qty}"
            )

        entry = post_movement(
            session,
            material_id=roll.material_id,
            movement_type=MovementType.issue,
            quantity=qty,
            roll_id=roll_id,
            warehouse=roll.warehouse,
            reference_type="cut_order",
            reference_id=cut_order.id,
            actor=actor,
        )
        session.add(
            CutIssue(
                cut_order_id=cut_order.id,
                roll_id=roll_id,
                quantity=qty,
                ledger_entry_id=entry.id,
            )
        )
        # Fully depleted roll → consumed.
        if balance(session, roll_id=roll_id) <= 0:
            roll.status = RollStatus.consumed
            session.add(roll)
        issued_total += qty

    cut_order.fabric_issued = quantize_qty(cut_order.fabric_issued + issued_total)
    if cut_order.status in (CutOrderStatus.planned, CutOrderStatus.fabric_reserved):
        cut_order.status = CutOrderStatus.in_cutting
    session.add(cut_order)
    session.flush()
    session.refresh(cut_order)
    return cut_order


def complete_cut_order(session: Session, cut_order: CutOrder, cut_qty: List) -> CutOrder:
    """Record pieces cut per size, complete the order, emit ProductionConfirmed."""
    cut_order_state_machine.assert_transition(
        cut_order.status.value, CutOrderStatus.completed.value
    )
    by_size = {s.size_label: s for s in cut_order.sizes}
    for c in cut_qty:
        row = by_size.get(c.size_label)
        if row is None:
            raise ProductionError(f"Size '{c.size_label}' is not on this cut order")
        row.cut_qty = c.planned_qty  # payload reuses CutSizeInput.planned_qty as actual
        session.add(row)

    total_pieces = sum(s.cut_qty for s in cut_order.sizes)
    cut_order.pieces_cut = total_pieces
    cut_order.status = CutOrderStatus.completed
    session.add(cut_order)
    session.flush()

    # Material cost of fabric issued, valued at the material's implied rate is
    # left to costing; here we report issued fabric so Finance/costing can value.
    material_cost = quantize_money(Decimal("0"))
    emit(
        ProductionConfirmed(
            cut_order_id=cut_order.id,
            order_number=cut_order.order_number,
            style_id=cut_order.style_id,
            pieces=total_pieces,
            material_cost=material_cost,
        )
    )
    session.refresh(cut_order)
    return cut_order


# --------------------------------------------------------------------------- #
# Sewing efficiency (5.5)
# --------------------------------------------------------------------------- #
def compute_efficiency(
    produced_qty: int, sam: Decimal, operators: int, working_minutes: int
) -> Decimal:
    """SAM-based line efficiency %:  produced × SAM / (operators × minutes) × 100."""
    capacity = Decimal(operators) * Decimal(working_minutes)
    if capacity <= 0 or sam is None or sam <= 0:
        return Decimal("0")
    eff = (Decimal(produced_qty) * sam) / capacity * Decimal("100")
    return eff.quantize(Decimal("0.01"))


def add_daily_output(
    session: Session, sewing_order: SewingOrder, payload
) -> SewingDailyOutput:
    if payload.produced_qty < 0:
        raise ProductionError("Produced quantity cannot be negative")
    efficiency = compute_efficiency(
        payload.produced_qty,
        sewing_order.sam or Decimal("0"),
        payload.operators,
        payload.working_minutes,
    )
    output = SewingDailyOutput(
        sewing_order_id=sewing_order.id,
        output_date=payload.output_date,
        shift=payload.shift,
        produced_qty=payload.produced_qty,
        operators=payload.operators,
        working_minutes=payload.working_minutes,
        efficiency_pct=efficiency,
        client_key=payload.client_key,
    )
    session.add(output)
    sewing_order.produced_qty += payload.produced_qty
    if sewing_order.status == SewingOrderStatus.planned:
        sewing_order.status = SewingOrderStatus.active
    session.add(sewing_order)
    session.flush()
    return output


def average_efficiency(sewing_order: SewingOrder) -> Decimal:
    outputs = sewing_order.daily_outputs
    if not outputs:
        return Decimal("0")
    total = sum((o.efficiency_pct for o in outputs), Decimal("0"))
    return (total / Decimal(len(outputs))).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- #
# Subcontracting (5.6)
# --------------------------------------------------------------------------- #
def receive_subcontract(
    session: Session, order: SubcontractOrder, received_qty: int
) -> SubcontractOrder:
    if received_qty <= 0:
        raise ProductionError("Received quantity must be positive")
    if order.received_qty + received_qty > order.sent_qty:
        raise ProductionError(
            f"Cannot receive {received_qty}; only "
            f"{order.sent_qty - order.received_qty} outstanding"
        )
    order.received_qty += received_qty
    if order.received_qty >= order.sent_qty:
        order.status = SubcontractStatus.received
    else:
        order.status = SubcontractStatus.partially_received
    session.add(order)
    session.flush()
    return order
