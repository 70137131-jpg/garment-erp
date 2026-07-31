"""11.2 — the MRP engine.

Textbook time-phased netting, with the pieces that matter in a garment factory:

1. **Gross requirements** — explode approved BOMs for every confirmed sales
   order and place the demand in the bucket containing its delivery date.
2. **Scheduled receipts** — outstanding purchase-order quantities, placed in the
   bucket containing the PO's expected date.
3. **Netting** — walk the buckets forward carrying a running projected balance.
   A negative projection creates a net requirement.
4. **Lot sizing** — a planned order is raised for at least the material's
   minimum order quantity.
5. **Lead-time offset** — the planned order's release date is its need date
   minus the material's lead time. If that lands in the past the order is
   flagged ``past_due``: it is already late before anyone has raised it.

Why this is not the existing per-order shortage calculation
------------------------------------------------------------
``sales.service.recalculate_material_requirements`` nets each order against the
*total* usable stock independently. Two orders needing the same fabric will both
report it as available, because neither knows about the other. That is fine for
"can this one order be covered right now", and wrong for planning.

Here the balance is consumed sequentially across the whole horizon, so the
second order sees what the first one already took. Demand is netted in delivery
date order, which is the closest thing to fairness that does not require a
priority scheme nobody has asked for yet.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlmodel import Session, select

from ..inventory.models import Reservation, ReservationStatus, StockLedgerEntry
from ..kernel.numbering import next_document_number
from ..kernel.types import quantize_qty
from ..masters.models import Material
from ..procurement.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    PurchaseRequisition,
    PurchaseRequisitionLine,
)
from ..sales.models import SalesOrder, SalesOrderStatus
from ..styles.models import BomStatus, BomVersion
from ..styles.service import material_requirements
from .models import (
    MrpBucket,
    MrpDemandSource,
    MrpRun,
    MrpRunCreate,
    MrpRunStatus,
    PlannedOrder,
    PlannedOrderStatus,
)

_ZERO = Decimal("0")

# Orders in these states still consume material; shipped/cancelled ones do not.
_OPEN_ORDER_STATES = (
    SalesOrderStatus.confirmed,
    SalesOrderStatus.in_production,
    SalesOrderStatus.partially_shipped,
)


class PlanningError(Exception):
    """Domain error building or firming a plan."""


# --------------------------------------------------------------------------- #
# Buckets
# --------------------------------------------------------------------------- #
def build_buckets(
    horizon_start: date, horizon_end: date, bucket_days: int
) -> List[Tuple[date, date]]:
    if bucket_days < 1:
        raise PlanningError("Bucket size must be at least one day")
    if horizon_end < horizon_start:
        raise PlanningError("Horizon end cannot precede horizon start")
    buckets: List[Tuple[date, date]] = []
    cursor = horizon_start
    while cursor <= horizon_end:
        end = min(cursor + timedelta(days=bucket_days - 1), horizon_end)
        buckets.append((cursor, end))
        cursor = end + timedelta(days=1)
    return buckets


def _bucket_index(buckets: List[Tuple[date, date]], when: Optional[date]) -> int:
    """Index of the bucket containing ``when``.

    Demand earlier than the horizon lands in bucket 0 — it is already due, and
    hiding it outside the window would be the one way to make a late order
    invisible. Demand beyond the horizon is dropped, not clamped, so the last
    bucket is not inflated with requirements it cannot explain.
    """
    if when is None:
        return 0
    if when < buckets[0][0]:
        return 0
    for index, (start, end) in enumerate(buckets):
        if start <= when <= end:
            return index
    return -1


# --------------------------------------------------------------------------- #
# Supply and demand gathering
# --------------------------------------------------------------------------- #
def on_hand_by_material(session: Session) -> Dict[int, Decimal]:
    """Ledger balance per material, less quantity already reserved.

    Reserved stock is physically present but spoken for, so counting it as
    available would let MRP under-order for everyone else.
    """
    balances: Dict[int, Decimal] = {}
    for material_id, qty in session.exec(
        select(
            StockLedgerEntry.material_id,
            func.coalesce(func.sum(StockLedgerEntry.quantity), 0),
        ).group_by(StockLedgerEntry.material_id)
    ).all():
        balances[int(material_id)] = quantize_qty(Decimal(qty))

    for material_id, qty in session.exec(
        select(
            Reservation.material_id,
            func.coalesce(func.sum(Reservation.reserved_qty), 0),
        )
        .where(Reservation.status == ReservationStatus.active)
        .group_by(Reservation.material_id)
    ).all():
        key = int(material_id)
        balances[key] = quantize_qty(balances.get(key, _ZERO) - Decimal(qty))
    return balances


def scheduled_receipts(
    session: Session, buckets: List[Tuple[date, date]]
) -> Dict[Tuple[int, int], Decimal]:
    """``{(material_id, bucket_index): outstanding_qty}`` from open POs."""
    rows = session.exec(
        select(
            PurchaseOrderLine.material_id,
            PurchaseOrderLine.ordered_qty,
            PurchaseOrderLine.received_qty,
            PurchaseOrder.expected_date,
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
        .where(
            PurchaseOrder.status.notin_(
                [PurchaseOrderStatus.cancelled, PurchaseOrderStatus.closed]
            )
        )
    ).all()

    receipts: Dict[Tuple[int, int], Decimal] = {}
    for material_id, ordered, received, expected in rows:
        outstanding = quantize_qty(Decimal(ordered) - Decimal(received))
        if outstanding <= 0:
            continue
        index = _bucket_index(buckets, expected)
        if index < 0:
            continue
        key = (int(material_id), index)
        receipts[key] = receipts.get(key, _ZERO) + outstanding
    return receipts


def gross_requirements(
    session: Session, buckets: List[Tuple[date, date]]
) -> Tuple[Dict[Tuple[int, int], Decimal], Dict[Tuple[int, int], List[Tuple[int, Decimal, Optional[date]]]]]:
    """Explode every open sales order into bucketed material demand.

    Returns the demand map plus, for each cell, the orders that produced it so
    a planner can always answer "why is this needed?".
    """
    orders = session.exec(
        select(SalesOrder)
        .where(SalesOrder.status.in_(_OPEN_ORDER_STATES))
        .order_by(SalesOrder.id)
    ).all()

    demand: Dict[Tuple[int, int], Decimal] = {}
    sources: Dict[Tuple[int, int], List[Tuple[int, Decimal, Optional[date]]]] = {}

    for order in orders:
        for line in order.lines:
            # Delivery dates are per line, not per order — a single order can
            # ship in waves, and bucketing the whole order on one date would
            # pull demand forward or push it back for every other line.
            need_date = line.delivery_date or order.order_date
            index = _bucket_index(buckets, need_date)
            if index < 0:
                continue  # delivery beyond the horizon
            bom = session.exec(
                select(BomVersion)
                .where(
                    BomVersion.style_id == line.style_id,
                    BomVersion.status == BomStatus.approved,
                )
                .order_by(BomVersion.version_no.desc())
            ).first()
            if bom is None:
                continue
            size_qty = {
                cell.size_label: (cell.confirmed_qty or cell.ordered_qty)
                for cell in line.cells
            }
            for material_id, qty in material_requirements(session, bom, size_qty).items():
                key = (int(material_id), index)
                demand[key] = quantize_qty(demand.get(key, _ZERO) + qty)
                sources.setdefault(key, []).append(
                    (order.id, quantize_qty(qty), need_date)
                )
    return demand, sources


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def run_mrp(session: Session, payload: MrpRunCreate, actor: str) -> MrpRun:
    """Net supply against demand across the horizon and persist the result."""
    horizon_start = payload.horizon_start or date.today()
    horizon_end = payload.horizon_end or (horizon_start + timedelta(days=90))
    buckets = build_buckets(horizon_start, horizon_end, payload.bucket_days)

    demand, sources = gross_requirements(session, buckets)
    receipts = scheduled_receipts(session, buckets)
    opening = on_hand_by_material(session)

    material_ids = sorted(
        {material_id for material_id, _ in demand}
        | {material_id for material_id, _ in receipts}
    )
    if not material_ids:
        raise PlanningError(
            "Nothing to plan: no open sales orders or purchase orders fall in this horizon"
        )

    run = MrpRun(
        run_number=next_document_number(session, "MRP_RUN", "MRP"),
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        bucket_days=payload.bucket_days,
        status=MrpRunStatus.draft,
        generated_by=actor,
        notes=payload.notes,
    )
    session.add(run)
    session.flush()

    today = date.today()
    for material_id in material_ids:
        material = session.get(Material, material_id)
        lead_time = material.lead_time_days if material else 0
        min_qty = material.min_order_qty if material else _ZERO
        balance = opening.get(material_id, _ZERO)

        for index, (bucket_start, bucket_end) in enumerate(buckets):
            gross = demand.get((material_id, index), _ZERO)
            receipt = receipts.get((material_id, index), _ZERO)
            opening_balance = balance

            projected = opening_balance + receipt - gross
            net_requirement = _ZERO
            planned_qty = _ZERO
            if projected < 0:
                net_requirement = quantize_qty(-projected)
                # Lot sizing: never order below the supplier's minimum.
                planned_qty = quantize_qty(max(net_requirement, min_qty or _ZERO))
                projected = projected + planned_qty

            balance = quantize_qty(projected)

            bucket_row = MrpBucket(
                run_id=run.id,
                material_id=material_id,
                sequence=index,
                bucket_start=bucket_start,
                bucket_end=bucket_end,
                opening_balance=quantize_qty(opening_balance),
                gross_requirement=gross,
                scheduled_receipts=receipt,
                net_requirement=net_requirement,
                planned_order_qty=planned_qty,
                projected_available=balance,
            )
            session.add(bucket_row)
            session.flush()

            for order_id, qty, need_date in sources.get((material_id, index), []):
                session.add(
                    MrpDemandSource(
                        bucket_id=bucket_row.id,
                        sales_order_id=order_id,
                        quantity=qty,
                        need_date=need_date,
                    )
                )

            if planned_qty > 0:
                release = bucket_start - timedelta(days=lead_time)
                session.add(
                    PlannedOrder(
                        run_id=run.id,
                        material_id=material_id,
                        quantity=planned_qty,
                        need_date=bucket_start,
                        release_date=release,
                        lead_time_days=lead_time,
                        status=PlannedOrderStatus.planned,
                        past_due=release < today,
                    )
                )

    session.flush()
    session.refresh(run)
    return run


# --------------------------------------------------------------------------- #
# Firming
# --------------------------------------------------------------------------- #
def firm_planned_orders(
    session: Session, run: MrpRun, planned_order_ids: List[int], actor: str
) -> PurchaseRequisition:
    """Convert planned orders into one purchase requisition.

    Firming is one-way and one-time per planned order: the requisition id is
    stamped back onto it, so a second firm cannot silently double-order.
    """
    if planned_order_ids:
        orders = session.exec(
            select(PlannedOrder).where(
                PlannedOrder.run_id == run.id,
                PlannedOrder.id.in_(planned_order_ids),
            )
        ).all()
        missing = set(planned_order_ids) - {o.id for o in orders}
        if missing:
            raise PlanningError(
                f"Planned orders not part of this run: {sorted(missing)}"
            )
    else:
        orders = session.exec(
            select(PlannedOrder).where(
                PlannedOrder.run_id == run.id,
                PlannedOrder.status == PlannedOrderStatus.planned,
            )
        ).all()

    firmable = [o for o in orders if o.status == PlannedOrderStatus.planned]
    if not firmable:
        raise PlanningError("No planned orders are available to firm")

    requisition = PurchaseRequisition(
        requisition_number=next_document_number(session, "PURCHASE_REQUISITION", "PR"),
        created_by=actor,
        notes=f"Firmed from MRP run {run.run_number}",
    )
    session.add(requisition)
    session.flush()

    for planned in firmable:
        material = session.get(Material, planned.material_id)
        session.add(
            PurchaseRequisitionLine(
                purchase_requisition_id=requisition.id,
                material_id=planned.material_id,
                requested_qty=planned.quantity,
                uom=material.base_uom if material else "metre",
            )
        )
        planned.status = PlannedOrderStatus.firmed
        planned.requisition_id = requisition.id
        session.add(planned)

    run.status = MrpRunStatus.firmed
    session.add(run)
    session.flush()
    return requisition
