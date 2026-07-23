"""Four-point inspection scoring and the roll-status gate."""

from decimal import Decimal

from sqlmodel import Session

from sqlmodel import select

from ..inventory.models import Roll, RollStatus
from ..kernel.numbering import next_document_number
from ..kernel.types import quantize_qty
from .aql import AqlError, single_sampling_plan
from .models import (
    FinalInspection,
    FinalInspectionCreate,
    FourPointDefect,
    FourPointInspection,
    FourPointInspectionCreate,
    InlineInspection,
    InlineInspectionCreate,
    InspectionResult,
)

_METRES_TO_YARDS = Decimal("1.09361")
_CM_TO_INCHES = Decimal("0.393701")


class QualityError(Exception):
    """Domain error running an inspection."""


def points_per_100sqyd(
    total_points: int, length_m: Decimal, width_cm: Decimal
) -> Decimal:
    length_yd = length_m * _METRES_TO_YARDS
    width_in = width_cm * _CM_TO_INCHES
    if length_yd <= 0 or width_in <= 0:
        return Decimal("0")
    score = (Decimal(total_points) * Decimal("3600")) / (length_yd * width_in)
    return quantize_qty(score)


def inspect_roll(
    session: Session, payload: FourPointInspectionCreate, actor: str
) -> FourPointInspection:
    """Run a four-point inspection and gate the roll's status accordingly.

    A roll must be pending inspection: re-inspecting an already-approved or
    consumed roll is rejected so the ledger's quality history stays coherent.
    """
    roll = session.get(Roll, payload.roll_id)
    if roll is None:
        raise QualityError("Roll not found")
    if roll.status != RollStatus.pending_inspection:
        raise QualityError(
            f"Roll {roll.roll_number} is '{roll.status.value}', not pending inspection"
        )

    length = payload.inspected_length or roll.length
    width = payload.width_cm or roll.width_cm
    if not width or width <= 0:
        raise QualityError("Fabric width is required to score a four-point inspection")

    for d in payload.defects:
        if not (1 <= d.penalty_points <= 4):
            raise QualityError("Defect penalty points must be between 1 and 4")

    total_points = sum(d.penalty_points for d in payload.defects)
    score = points_per_100sqyd(total_points, length, width)
    passed = score <= payload.acceptance_threshold

    number = next_document_number(session, "INSPECTION", "FPI")
    inspection = FourPointInspection(
        inspection_number=number,
        roll_id=roll.id,
        inspected_length=quantize_qty(length),
        width_cm=quantize_qty(width),
        acceptance_threshold=payload.acceptance_threshold,
        total_points=total_points,
        points_per_100sqyd=score,
        result=InspectionResult.passed if passed else InspectionResult.failed,
        inspected_date=payload.inspected_date,
        inspector=actor,
    )
    session.add(inspection)
    session.flush()

    for d in payload.defects:
        session.add(
            FourPointDefect(
                inspection_id=inspection.id,
                description=d.description,
                defect_code=d.defect_code,
                category=d.category,
                penalty_points=d.penalty_points,
                position_m=d.position_m,
            )
        )

    # The gate: approved → available, failed → quarantined (7.1 / 7.5).
    roll.status = RollStatus.available if passed else RollStatus.quarantined
    session.add(roll)
    session.flush()
    return inspection


# --------------------------------------------------------------------------- #
# 7.3 Inline DHU
# --------------------------------------------------------------------------- #
def record_inline(
    session: Session, payload: InlineInspectionCreate, actor: str
) -> InlineInspection:
    if payload.units_checked <= 0:
        raise QualityError("Units checked must be positive")
    if payload.defects_found < 0:
        raise QualityError("Defects found cannot be negative")
    dhu = quantize_qty(
        (Decimal(payload.defects_found) / Decimal(payload.units_checked)) * Decimal("100")
    )
    number = next_document_number(session, "INLINE_INSPECTION", "DHU")
    inspection = InlineInspection(
        inspection_number=number,
        sewing_order_id=payload.sewing_order_id,
        inspected_date=payload.inspected_date,
        units_checked=payload.units_checked,
        defects_found=payload.defects_found,
        dhu=dhu,
        inspector=actor,
    )
    session.add(inspection)
    session.flush()
    return inspection


# --------------------------------------------------------------------------- #
# 7.4 Final AQL
# --------------------------------------------------------------------------- #
def record_final(
    session: Session, payload: FinalInspectionCreate, actor: str
) -> FinalInspection:
    try:
        code, sample_size, ac, re_num = single_sampling_plan(payload.lot_size, payload.aql)
    except AqlError as exc:
        raise QualityError(str(exc)) from exc
    if payload.defects_found < 0:
        raise QualityError("Defects found cannot be negative")

    passed = payload.defects_found <= ac
    number = next_document_number(session, "FINAL_INSPECTION", "AQL")
    inspection = FinalInspection(
        inspection_number=number,
        sales_order_id=payload.sales_order_id,
        lot_size=payload.lot_size,
        aql=payload.aql,
        code_letter=code,
        sample_size=sample_size,
        accept_number=ac,
        reject_number=re_num,
        defects_found=payload.defects_found,
        result=InspectionResult.passed if passed else InspectionResult.failed,
        inspected_date=payload.inspected_date,
        inspector=actor,
    )
    session.add(inspection)
    session.flush()
    return inspection


def order_has_passed_final(session: Session, sales_order_id: int) -> bool:
    """Shipment gate: an order may ship only with a passed final inspection."""
    row = session.exec(
        select(FinalInspection).where(
            FinalInspection.sales_order_id == sales_order_id,
            FinalInspection.result == InspectionResult.passed,
        )
    ).first()
    return row is not None
