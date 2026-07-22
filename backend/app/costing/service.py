"""Cost-sheet roll-up maths, version lifecycle, and order profitability."""

from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from ..kernel.audit import utcnow
from ..kernel.state_machine import StateMachine
from ..kernel.types import quantize_money
from ..styles.models import Style
from .models import (
    CostCategory,
    CostLine,
    CostSheetCreate,
    CostSheetStatus,
    CostSheetVersion,
)

cost_sheet_state_machine = StateMachine(
    transitions={
        CostSheetStatus.draft.value: {CostSheetStatus.approved.value},
        CostSheetStatus.approved.value: {CostSheetStatus.superseded.value},
        CostSheetStatus.superseded.value: set(),
    },
    initial=CostSheetStatus.draft.value,
)


class CostingError(Exception):
    """Domain error building or approving a cost sheet."""


def line_amount(line: CostLine) -> Decimal:
    return quantize_money(line.quantity * line.rate * (Decimal("1") + (line.wastage_pct or Decimal("0"))))


def sewing_cost(sheet: CostSheetVersion) -> Decimal:
    """SAM × cost/min ÷ efficiency (6.4)."""
    eff = sheet.sewing_efficiency_pct or Decimal("0")
    if eff <= 0 or sheet.sam <= 0:
        return Decimal("0.00")
    return quantize_money(sheet.sam * sheet.sewing_cost_per_min / (eff / Decimal("100")))


def rollup(sheet: CostSheetVersion) -> dict:
    """Compute the full cost breakdown for a cost sheet."""
    material_cost = quantize_money(
        sum((line_amount(l) for l in sheet.lines), Decimal("0"))
    )
    sew = sewing_cost(sheet)
    base = material_cost + sew
    overhead = quantize_money(base * (sheet.overhead_pct or Decimal("0")))
    total = quantize_money(base + overhead)

    margin = sheet.margin_pct or Decimal("0")
    if margin >= Decimal("1"):
        raise CostingError("Margin percentage must be below 100%")
    selling_price = quantize_money(total / (Decimal("1") - margin)) if margin > 0 else total
    margin_amount = quantize_money(selling_price - total)

    return {
        "material_cost": material_cost,
        "sewing_cost": sew,
        "overhead_cost": overhead,
        "total_cost": total,
        "selling_price": selling_price,
        "margin_amount": margin_amount,
    }


def create_cost_sheet(
    session: Session, style: Style, payload: CostSheetCreate
) -> CostSheetVersion:
    last = session.exec(
        select(CostSheetVersion)
        .where(CostSheetVersion.style_id == style.id)
        .order_by(CostSheetVersion.version_no.desc())
    ).first()
    next_no = (last.version_no + 1) if last else 1

    sheet = CostSheetVersion(
        style_id=style.id,
        version_no=next_no,
        status=CostSheetStatus.draft,
        base_size=payload.base_size,
        currency=payload.currency,
        sam=payload.sam,
        sewing_cost_per_min=payload.sewing_cost_per_min,
        sewing_efficiency_pct=payload.sewing_efficiency_pct,
        overhead_pct=payload.overhead_pct,
        margin_pct=payload.margin_pct,
        notes=payload.notes,
    )
    session.add(sheet)
    session.flush()
    for line_in in payload.lines:
        session.add(
            CostLine(
                cost_sheet_version_id=sheet.id,
                category=line_in.category,
                description=line_in.description,
                material_id=line_in.material_id,
                quantity=line_in.quantity,
                rate=line_in.rate,
                wastage_pct=line_in.wastage_pct,
            )
        )
    session.flush()
    session.refresh(sheet)
    return sheet


def approve_cost_sheet(
    session: Session, sheet: CostSheetVersion, actor: str
) -> CostSheetVersion:
    cost_sheet_state_machine.assert_transition(
        sheet.status.value, CostSheetStatus.approved.value
    )
    prior = session.exec(
        select(CostSheetVersion).where(
            CostSheetVersion.style_id == sheet.style_id,
            CostSheetVersion.status == CostSheetStatus.approved,
        )
    ).all()
    for old in prior:
        old.status = CostSheetStatus.superseded
        session.add(old)
    sheet.status = CostSheetStatus.approved
    sheet.approved_by = actor
    sheet.approved_at = utcnow().isoformat()
    session.add(sheet)
    session.flush()
    return sheet


def current_cost_sheet(session: Session, style_id: int) -> Optional[CostSheetVersion]:
    return session.exec(
        select(CostSheetVersion).where(
            CostSheetVersion.style_id == style_id,
            CostSheetVersion.status == CostSheetStatus.approved,
        )
    ).first()
