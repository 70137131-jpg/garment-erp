"""14.2 — marker geometry and cut-plan persistence."""

from decimal import Decimal
from typing import Dict, List, Optional

from sqlmodel import Session, select

from ..kernel.numbering import next_document_number
from ..kernel.types import quantize_qty
from ..sales.models import SalesOrder, SalesOrderLine, SalesOrderSizeCell
from ..styles.models import BomStatus, BomVersion, Style
from ..styles.service import material_requirements
from .models import (
    CutPlan,
    CutPlanCreate,
    CutPlanDemand,
    CutPlanLay,
    CutPlanStatus,
    Marker,
    MarkerCreate,
    MarkerSize,
    MarkerStatus,
)
from .solver import MarkerSpec, SolverError, solve_lay_plan, weighted_efficiency

_ZERO = Decimal("0")


class MarkerError(Exception):
    """Domain error defining a marker or solving a plan."""


def marker_efficiency(
    pattern_area_cm2: Decimal, length_cm: Decimal, width_cm: Decimal
) -> Decimal:
    """Fraction of the marker rectangle filled by pattern pieces.

    Above 100% is geometrically impossible — the pieces would have to overlap —
    so it is rejected rather than stored. The usual cause is a pattern area
    measured for a different size range.
    """
    rectangle = Decimal(length_cm) * Decimal(width_cm)
    if rectangle <= 0:
        raise MarkerError("Marker length and width must both be positive")
    if pattern_area_cm2 <= 0:
        raise MarkerError("Pattern area must be positive")
    efficiency = Decimal(pattern_area_cm2) / rectangle * Decimal("100")
    if efficiency > Decimal("100"):
        raise MarkerError(
            f"Pattern area exceeds the marker rectangle ({efficiency:.1f}%): "
            "the pieces cannot fit"
        )
    return efficiency.quantize(Decimal("0.000001"))


def create_marker(session: Session, payload: MarkerCreate) -> Marker:
    if session.get(Style, payload.style_id) is None:
        raise MarkerError("Style not found")
    code = payload.marker_code.strip().upper()
    if not code:
        raise MarkerError("Marker code is required")
    if session.exec(select(Marker).where(Marker.marker_code == code)).first():
        raise MarkerError("Marker code already exists")
    if not payload.sizes:
        raise MarkerError("A marker must contain at least one size")
    if any(s.quantity <= 0 for s in payload.sizes):
        raise MarkerError("Every size in a marker must have a positive quantity")
    labels = [s.size_label for s in payload.sizes]
    if len(labels) != len(set(labels)):
        raise MarkerError("A size may appear only once in a marker")
    if payload.max_plies < 1:
        raise MarkerError("Maximum plies must be at least one")

    efficiency = marker_efficiency(
        payload.pattern_area_cm2, payload.length_cm, payload.width_cm
    )
    marker = Marker(
        marker_code=code,
        style_id=payload.style_id,
        bom_version_id=payload.bom_version_id,
        width_cm=quantize_qty(payload.width_cm),
        length_cm=quantize_qty(payload.length_cm),
        pattern_area_cm2=quantize_qty(payload.pattern_area_cm2),
        efficiency_pct=efficiency,
        max_plies=payload.max_plies,
        notes=payload.notes,
        status=MarkerStatus.draft,
    )
    session.add(marker)
    session.flush()
    for size in payload.sizes:
        session.add(
            MarkerSize(
                marker_id=marker.id,
                size_label=size.size_label,
                quantity=size.quantity,
            )
        )
    session.flush()
    session.refresh(marker)
    return marker


def _order_demand(session: Session, sales_order_id: int, style_id: int) -> Dict[str, int]:
    order = session.get(SalesOrder, sales_order_id)
    if order is None:
        raise MarkerError("Sales order not found")
    demand: Dict[str, int] = {}
    for line in order.lines:
        if line.style_id != style_id:
            continue
        for cell in line.cells:
            qty = cell.confirmed_qty or cell.ordered_qty
            if qty:
                demand[cell.size_label] = demand.get(cell.size_label, 0) + qty
    if not demand:
        raise MarkerError("This order has no quantity for the requested style")
    return demand


def _bom_fabric_cm(
    session: Session, style_id: int, demand: Dict[str, int], fabric_material_id: Optional[int]
) -> Optional[Decimal]:
    """What the BOM says the same garments should consume, in centimetres.

    Used only for comparison — the marker plan is the operational number. BOM
    consumption is per-garment metres, so it is converted to centimetres to sit
    beside the marker figures.
    """
    if fabric_material_id is None:
        return None
    bom = session.exec(
        select(BomVersion)
        .where(BomVersion.style_id == style_id, BomVersion.status == BomStatus.approved)
        .order_by(BomVersion.version_no.desc())
    ).first()
    if bom is None:
        return None
    requirements = material_requirements(session, bom, demand)
    metres = requirements.get(fabric_material_id)
    if metres is None:
        return None
    return quantize_qty(Decimal(metres) * Decimal("100"))


def build_cut_plan(session: Session, payload: CutPlanCreate) -> CutPlan:
    style = session.get(Style, payload.style_id)
    if style is None:
        raise MarkerError("Style not found")
    if payload.width_cm <= 0:
        raise MarkerError("Fabric width must be positive")

    if payload.sizes:
        demand = {s.size_label: s.quantity for s in payload.sizes if s.quantity > 0}
    elif payload.sales_order_id is not None:
        demand = _order_demand(session, payload.sales_order_id, payload.style_id)
    else:
        raise MarkerError("Provide sizes, or a sales order to take them from")
    if not demand:
        raise MarkerError("There is nothing to cut")

    # Candidate markers: approved, for this style, nested at this fabric width.
    stmt = select(Marker).where(
        Marker.style_id == payload.style_id,
        Marker.status == MarkerStatus.approved,
        Marker.width_cm == quantize_qty(payload.width_cm),
    )
    if payload.marker_ids:
        stmt = stmt.where(Marker.id.in_(payload.marker_ids))
    markers = session.exec(stmt).all()
    if not markers:
        raise MarkerError(
            "No approved markers exist for this style at this fabric width"
        )

    specs = [
        MarkerSpec(
            marker_id=m.id,
            code=m.marker_code,
            length_cm=m.length_cm,
            ratio={s.size_label: s.quantity for s in m.sizes},
            max_plies=m.max_plies,
            efficiency_pct=m.efficiency_pct,
        )
        for m in markers
    ]

    max_plies = payload.max_plies or max(m.max_plies for m in markers)
    try:
        lays, produced = solve_lay_plan(demand, specs, max_plies)
    except SolverError as exc:
        raise MarkerError(str(exc)) from exc

    overcut = {
        size: max(0, produced.get(size, 0) - qty) for size, qty in demand.items()
    }
    # Sizes produced that were never asked for are overcut too.
    for size, qty in produced.items():
        if size not in demand:
            overcut[size] = overcut.get(size, 0) + qty

    if not payload.allow_overcut and any(v > 0 for v in overcut.values()):
        detail = ", ".join(f"{s}+{v}" for s, v in sorted(overcut.items()) if v > 0)
        raise MarkerError(
            f"No exact-fit plan exists with these markers (overcut would be {detail})"
        )

    total_fabric = sum((l.fabric_cm for l in lays), _ZERO)
    plan = CutPlan(
        plan_number=next_document_number(session, "CUT_PLAN", "CP"),
        style_id=payload.style_id,
        sales_order_id=payload.sales_order_id,
        cut_order_id=payload.cut_order_id,
        fabric_material_id=payload.fabric_material_id,
        width_cm=quantize_qty(payload.width_cm),
        max_plies=max_plies,
        status=CutPlanStatus.draft,
        total_fabric_cm=quantize_qty(total_fabric),
        total_plies=sum(l.plies for l in lays),
        lay_count=len(lays),
        required_pieces=sum(demand.values()),
        planned_pieces=sum(produced.values()),
        overcut_pieces=sum(overcut.values()),
        weighted_efficiency_pct=weighted_efficiency(lays),
        bom_fabric_cm=_bom_fabric_cm(
            session, payload.style_id, demand, payload.fabric_material_id
        ),
        notes=payload.notes,
    )
    session.add(plan)
    session.flush()

    for lay in lays:
        session.add(
            CutPlanLay(
                plan_id=plan.id,
                marker_id=lay.marker.marker_id,
                sequence=lay.sequence,
                plies=lay.plies,
                fabric_cm=quantize_qty(lay.fabric_cm),
                pieces=lay.pieces,
            )
        )
    for size in sorted(set(demand) | set(produced)):
        session.add(
            CutPlanDemand(
                plan_id=plan.id,
                size_label=size,
                required_qty=demand.get(size, 0),
                planned_qty=produced.get(size, 0),
                overcut_qty=overcut.get(size, 0),
            )
        )
    session.flush()
    session.refresh(plan)
    return plan
