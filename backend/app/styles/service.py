"""BOM version lifecycle + the per-size fabric-requirement calculation.

This is deliberately a service (not router) module so production (Module 5) and
costing (Module 6) can reuse the exact same size-aware maths the BOM defines.
"""

from decimal import Decimal
from typing import Dict, Mapping

from sqlmodel import Session, select

from ..kernel.audit import utcnow
from ..kernel.state_machine import StateMachine
from ..kernel.types import quantize_qty
from ..masters.models import Material, SizeRange, SizeRangeItem
from .models import (
    BomLine,
    BomLineSizeConsumption,
    BomStatus,
    BomVersion,
    BomVersionCreate,
    Style,
)

# draft can be approved; an approved version is superseded when a newer one is
# approved. There is no route back to draft — corrections are new versions.
bom_state_machine = StateMachine(
    transitions={
        BomStatus.draft.value: {BomStatus.approved.value},
        BomStatus.approved.value: {BomStatus.superseded.value},
        BomStatus.superseded.value: set(),
    },
    initial=BomStatus.draft.value,
)


class BomError(Exception):
    """Domain error building or approving a BOM (bad size label, wrong state…)."""


def _size_items_by_label(session: Session, size_range_id: int) -> Dict[str, SizeRangeItem]:
    items = session.exec(
        select(SizeRangeItem).where(SizeRangeItem.size_range_id == size_range_id)
    ).all()
    return {item.label: item for item in items}


def create_bom_version(
    session: Session, style: Style, payload: BomVersionCreate
) -> BomVersion:
    """Create a new *draft* BOM version for a style.

    Version numbers increment per style. Every size-consumption cell is validated
    against the style's size range so the normalized grid can never reference a
    size the style doesn't have.
    """
    size_items = _size_items_by_label(session, style.size_range_id)
    if not size_items:
        raise BomError("Style's size range has no sizes defined")

    last = session.exec(
        select(BomVersion)
        .where(BomVersion.style_id == style.id)
        .order_by(BomVersion.version_no.desc())
    ).first()
    next_no = (last.version_no + 1) if last else 1

    version = BomVersion(style_id=style.id, version_no=next_no, status=BomStatus.draft)
    session.add(version)
    session.flush()

    seen_materials: set[int] = set()
    for line_in in payload.lines:
        material = session.get(Material, line_in.material_id)
        if material is None:
            raise BomError(f"Material {line_in.material_id} not found")
        if line_in.material_id in seen_materials:
            raise BomError(f"Material {material.code} appears twice in the BOM")
        seen_materials.add(line_in.material_id)

        line = BomLine(
            bom_version_id=version.id,
            material_id=line_in.material_id,
            colour_id=line_in.colour_id,
            wastage_pct=line_in.wastage_pct,
            optional_component=line_in.optional_component,
            notes=line_in.notes,
        )
        session.add(line)
        session.flush()

        for cell in line_in.size_consumption:
            item = size_items.get(cell.size_label)
            if item is None:
                raise BomError(
                    f"Size '{cell.size_label}' is not in the style's size range"
                )
            session.add(
                BomLineSizeConsumption(
                    bom_line_id=line.id,
                    size_range_item_id=item.id,
                    position=item.position,
                    size_label=item.label,
                    consumption=cell.consumption,
                )
            )
    session.flush()
    session.refresh(version)
    return version


def approve_bom_version(session: Session, version: BomVersion, actor: str) -> BomVersion:
    """Approve a draft version and supersede the style's prior approved one."""
    bom_state_machine.assert_transition(version.status.value, BomStatus.approved.value)

    prior = session.exec(
        select(BomVersion).where(
            BomVersion.style_id == version.style_id,
            BomVersion.status == BomStatus.approved,
        )
    ).all()
    for old in prior:
        bom_state_machine.assert_transition(
            old.status.value, BomStatus.superseded.value
        )
        old.status = BomStatus.superseded
        session.add(old)

    version.status = BomStatus.approved
    version.approved_by = actor
    version.approved_at = utcnow().isoformat()
    session.add(version)
    session.flush()
    return version


def current_bom_version(session: Session, style_id: int) -> BomVersion | None:
    """The style's live recipe: its single approved version, if any."""
    return session.exec(
        select(BomVersion).where(
            BomVersion.style_id == style_id,
            BomVersion.status == BomStatus.approved,
        )
    ).first()


def material_requirements(
    session: Session,
    bom_version: BomVersion,
    size_quantities: Mapping[str, int],
    *,
    include_optional: bool = True,
) -> Dict[int, Decimal]:
    """Total requirement per material for a given size breakdown.

    For each line: sum over sizes of ``consumption(size) × qty(size)``, then apply
    the line's wastage %. Returns ``{material_id: quantity}`` in each material's
    base UoM. This is the single source of the fabric-requirement rule (5.2) and
    is reused by material costing (6.3).
    """
    totals: Dict[int, Decimal] = {}
    for line in bom_version.lines:
        if line.optional_component and not include_optional:
            continue
        base = Decimal("0")
        for cell in line.size_consumption:
            qty = size_quantities.get(cell.size_label, 0)
            if qty:
                base += cell.consumption * Decimal(qty)
        if base == 0:
            continue
        with_waste = base * (Decimal("1") + (line.wastage_pct or Decimal("0")))
        totals[line.material_id] = totals.get(line.material_id, Decimal("0")) + quantize_qty(
            with_waste
        )
    return totals
