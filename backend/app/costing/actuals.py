"""6.8 — Actual costing and variance analysis.

The cost sheet is the *standard*. What the factory actually consumed is already
captured by operations and needs no new data entry:

* **material** — valued stock issues against the order's cut orders. The ledger
  already carries FIFO/weighted cost per issue, so the money is not re-derived
  here, only attributed.
* **labour** — sewing minutes from daily output (``operators × working_minutes``)
  priced at the actual wage rate. Piece-rate earnings are used when the
  workforce module has them, because those are the money that genuinely left
  the business.
* **subcontract** — received quantity × agreed rate on subcontract orders.
* **overhead** — an absorbed figure supplied by finance.

The gap between the two is then decomposed the classic way, so that

    actual_total − standard_total  ==  Σ variances

holds **exactly**. That identity is asserted in the tests. A variance report
whose parts do not reconstruct the whole is worse than no report, because it
invites people to trust a number that has quietly lost money somewhere.

To keep the identity true in the awkward cases, every variance is expressed as
a *residual* rather than the textbook product form:

    material price      = actual_cost − actual_qty × standard_rate
    material usage      = (actual_qty − standard_qty) × standard_rate
    labour rate         = actual_labour_cost − standard_rate × actual_minutes
    labour efficiency   = (actual_minutes − standard_minutes) × standard_rate

These agree with the textbook formulas whenever the inputs are consistent, and
still telescope correctly when they are not (nothing issued, zero minutes
recorded, a material consumed that no cost sheet anticipated).

Sign convention throughout: **positive is adverse** (actual exceeded standard,
a debit), negative is favourable.
"""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlmodel import Session, select

from ..inventory.models import MovementType, StockLedgerEntry
from ..kernel.audit import utcnow
from ..kernel.numbering import next_document_number
from ..kernel.types import quantize_money, quantize_qty
from ..production.models import (
    CutOrder,
    SewingDailyOutput,
    SewingOrder,
    SubcontractOrder,
)
from ..sales.models import SalesOrder, SalesOrderLine
from ..workforce.models import PieceWorkRecord
from .models import (
    ActualCostRun,
    ActualCostRunCreate,
    ActualCostRunStatus,
    CostCategory,
    CostSheetVersion,
    CostVariance,
    VarianceType,
)
from .service import CostingError, current_cost_sheet, line_amount, rollup

_ZERO = Decimal("0")


# --------------------------------------------------------------------------- #
# Gathering actuals
# --------------------------------------------------------------------------- #
def _cut_order_ids(session: Session, sales_order_id: int) -> List[int]:
    return list(
        session.exec(
            select(CutOrder.id).where(CutOrder.sales_order_id == sales_order_id)
        ).all()
    )


def produced_quantity(session: Session, sales_order_id: int) -> int:
    """Garments actually produced for the order.

    Sewing output is the truth once sewing has started, because that is the
    stage that creates a sellable garment. Before then, pieces cut is the best
    signal available.
    """
    cut_ids = _cut_order_ids(session, sales_order_id)
    if not cut_ids:
        return 0
    sewn = session.exec(
        select(func.coalesce(func.sum(SewingOrder.produced_qty), 0)).where(
            SewingOrder.cut_order_id.in_(cut_ids)
        )
    ).one()
    if sewn:
        return int(sewn)
    cut = session.exec(
        select(func.coalesce(func.sum(CutOrder.pieces_cut), 0)).where(
            CutOrder.id.in_(cut_ids)
        )
    ).one()
    return int(cut or 0)


def actual_material_by_item(
    session: Session, sales_order_id: int
) -> Dict[int, Tuple[Decimal, Decimal]]:
    """``{material_id: (issued_qty, issued_cost)}`` for the order's cut orders.

    Issue rows are negative in the ledger; both figures come back positive so
    callers need not remember the sign.
    """
    cut_ids = _cut_order_ids(session, sales_order_id)
    if not cut_ids:
        return {}
    rows = session.exec(
        select(
            StockLedgerEntry.material_id,
            func.coalesce(func.sum(StockLedgerEntry.quantity), 0),
            func.coalesce(func.sum(StockLedgerEntry.extended_cost), 0),
        )
        .where(
            StockLedgerEntry.reference_type == "cut_order",
            StockLedgerEntry.reference_id.in_(cut_ids),
            StockLedgerEntry.movement_type == MovementType.issue,
        )
        .group_by(StockLedgerEntry.material_id)
    ).all()
    return {
        int(material_id): (
            quantize_qty(-Decimal(qty)),
            quantize_money(-Decimal(cost)),
        )
        for material_id, qty, cost in rows
    }


def actual_minutes(session: Session, sales_order_id: int) -> Decimal:
    """Attended sewing minutes: Σ operators × working_minutes."""
    cut_ids = _cut_order_ids(session, sales_order_id)
    if not cut_ids:
        return _ZERO
    total = session.exec(
        select(
            func.coalesce(
                func.sum(
                    SewingDailyOutput.operators * SewingDailyOutput.working_minutes
                ),
                0,
            )
        )
        .join(SewingOrder, SewingOrder.id == SewingDailyOutput.sewing_order_id)
        .where(SewingOrder.cut_order_id.in_(cut_ids))
    ).one()
    return quantize_qty(Decimal(total or 0))


def actual_piecework_cost(session: Session, sales_order_id: int) -> Decimal:
    """Piece-rate earnings booked against the styles on this order.

    Piece work is recorded per style, not per order, so this is an attribution
    rather than a direct link. It is only trusted when the caller has not
    supplied an explicit rate.
    """
    style_ids = list(
        session.exec(
            select(SalesOrderLine.style_id).where(
                SalesOrderLine.sales_order_id == sales_order_id
            )
        ).all()
    )
    if not style_ids:
        return _ZERO
    total = session.exec(
        select(
            func.coalesce(func.sum(PieceWorkRecord.pieces * PieceWorkRecord.rate), 0)
        ).where(PieceWorkRecord.style_id.in_(style_ids))
    ).one()
    return quantize_money(Decimal(total or 0))


def actual_subcontract_cost(session: Session, sales_order_id: int) -> Decimal:
    cut_ids = _cut_order_ids(session, sales_order_id)
    if not cut_ids:
        return _ZERO
    total = session.exec(
        select(
            func.coalesce(
                func.sum(SubcontractOrder.received_qty * SubcontractOrder.rate), 0
            )
        ).where(SubcontractOrder.cut_order_id.in_(cut_ids))
    ).one()
    return quantize_money(Decimal(total or 0))


# --------------------------------------------------------------------------- #
# Standard side
# --------------------------------------------------------------------------- #
def _standard_material_per_garment(
    sheet: CostSheetVersion,
) -> Dict[Optional[int], Tuple[Decimal, Decimal]]:
    """``{material_id: (qty_incl_wastage, rate)}`` per garment.

    Lines with no linked material are keyed ``None``: they carry a money
    standard that the stock ledger cannot be matched against.
    """
    out: Dict[Optional[int], Tuple[Decimal, Decimal]] = {}
    for line in sheet.lines:
        if line.category not in (CostCategory.material, CostCategory.trim):
            continue
        qty = line.quantity * (Decimal("1") + (line.wastage_pct or _ZERO))
        key = line.material_id
        prior_qty, _ = out.get(key, (_ZERO, _ZERO))
        # Repeated lines for one material accumulate quantity; the last rate
        # wins, matching how the roll-up already values them.
        out[key] = (prior_qty + qty, line.rate)
    return out


def _standard_subcontract_per_garment(sheet: CostSheetVersion) -> Decimal:
    return quantize_money(
        sum(
            (line_amount(l) for l in sheet.lines if l.category == CostCategory.other),
            _ZERO,
        )
    )


# --------------------------------------------------------------------------- #
# Material variance decomposition
# --------------------------------------------------------------------------- #
class _MaterialResult:
    __slots__ = (
        "price_variance",
        "usage_variance",
        "std_cost",
        "std_qty",
        "actual_qty",
        "std_rate",
        "notes",
    )

    def __init__(self, price_variance, usage_variance, std_cost, std_qty, actual_qty, std_rate, notes):
        self.price_variance = price_variance
        self.usage_variance = usage_variance
        self.std_cost = std_cost
        self.std_qty = std_qty
        self.actual_qty = actual_qty
        self.std_rate = std_rate
        self.notes = notes


def _material_variances(
    sheet: CostSheetVersion,
    produced_qty: int,
    actuals: Dict[int, Tuple[Decimal, Decimal]],
) -> _MaterialResult:
    """Split the material gap into price and usage.

    Every standard line and every issued material contributes to exactly one
    side of each formula, so the two variances always sum to
    ``actual_material_cost − std_material_cost``.
    """
    standards = _standard_material_per_garment(sheet)
    scale = Decimal(produced_qty)

    price_variance = _ZERO
    usage_variance = _ZERO
    std_cost = _ZERO
    std_qty_total = _ZERO
    actual_qty_total = _ZERO
    notes: List[str] = []

    for material_id, (per_garment_qty, rate) in standards.items():
        std_qty = quantize_qty(per_garment_qty * scale)
        line_std_cost = quantize_money(std_qty * rate)
        std_cost += line_std_cost

        if material_id is None:
            # A money-only standard with no ledger counterpart. Nothing can be
            # matched to it, so the whole amount lands in usage as favourable
            # and is called out — otherwise it would silently break the
            # reconciliation identity.
            usage_variance -= line_std_cost
            if line_std_cost:
                notes.append("cost-sheet line with no linked material is untracked")
            continue

        std_qty_total += std_qty
        actual_qty, actual_cost = actuals.get(material_id, (_ZERO, _ZERO))
        actual_qty_total += actual_qty
        price_variance += actual_cost - quantize_money(actual_qty * rate)
        usage_variance += quantize_money((actual_qty - std_qty) * rate)
        if material_id not in actuals and std_qty > 0:
            notes.append(f"material {material_id} planned but never issued")

    # Anything issued with no standard is wholly unplanned consumption.
    for material_id, (actual_qty, actual_cost) in actuals.items():
        if material_id in standards:
            continue
        actual_qty_total += actual_qty
        usage_variance += actual_cost
        notes.append(f"material {material_id} issued without a cost-sheet standard")

    std_qty_total = quantize_qty(std_qty_total)
    std_cost = quantize_money(std_cost)
    return _MaterialResult(
        price_variance=quantize_money(price_variance),
        usage_variance=quantize_money(usage_variance),
        std_cost=std_cost,
        std_qty=std_qty_total,
        actual_qty=quantize_qty(actual_qty_total),
        std_rate=quantize_money(std_cost / std_qty_total) if std_qty_total > 0 else _ZERO,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Run builder
# --------------------------------------------------------------------------- #
def build_actual_cost_run(
    session: Session,
    order: SalesOrder,
    payload: ActualCostRunCreate,
) -> ActualCostRun:
    """Gather actuals, compare against standard, and persist a decomposed run."""
    if not order.lines:
        raise CostingError("Sales order has no lines to cost")
    style_ids = {line.style_id for line in order.lines}
    if len(style_ids) > 1:
        raise CostingError(
            "Actual costing runs on single-style orders; split the order to cost it"
        )
    sheet = current_cost_sheet(session, next(iter(style_ids)))
    if sheet is None:
        raise CostingError("No approved cost sheet exists for this style")

    qty = produced_quantity(session, order.id)
    if qty <= 0:
        raise CostingError(
            "Nothing has been produced for this order yet — there is no actual to cost"
        )

    standard = rollup(sheet)
    scale = Decimal(qty)

    # ---- material -------------------------------------------------------- #
    actual_materials = actual_material_by_item(session, order.id)
    actual_material_cost = quantize_money(
        sum((cost for _, cost in actual_materials.values()), _ZERO)
    )
    material = _material_variances(sheet, qty, actual_materials)

    # ---- labour ---------------------------------------------------------- #
    std_labour_cost = quantize_money(standard["sewing_cost"] * scale)
    std_rate = sheet.sewing_cost_per_min or _ZERO
    # Standard minutes *allowed* — including the efficiency the standard itself
    # assumes — so that std_minutes × std_rate reproduces std_labour_cost.
    efficiency = (sheet.sewing_efficiency_pct or _ZERO) / Decimal("100")
    std_minutes = (
        quantize_qty(sheet.sam / efficiency * scale)
        if efficiency > 0 and sheet.sam
        else _ZERO
    )
    act_minutes = actual_minutes(session, order.id)

    if payload.actual_rate_per_min is not None:
        actual_rate = payload.actual_rate_per_min
        actual_labour_cost = quantize_money(actual_rate * act_minutes)
    else:
        piecework = actual_piecework_cost(session, order.id)
        if piecework > 0:
            actual_labour_cost = piecework
            actual_rate = quantize_money(piecework / act_minutes) if act_minutes > 0 else _ZERO
        else:
            # No independent evidence of the wage rate: hold the rate at
            # standard so the entire labour gap surfaces as efficiency, rather
            # than inventing a rate variance nobody can defend.
            actual_rate = std_rate
            actual_labour_cost = quantize_money(std_rate * act_minutes)

    # Residual form: guarantees rate + efficiency == actual − standard even
    # when no minutes were recorded but wages were genuinely paid.
    labour_efficiency_var = quantize_money((act_minutes - std_minutes) * std_rate)
    labour_rate_var = quantize_money(
        actual_labour_cost - std_labour_cost - labour_efficiency_var
    )

    # ---- overhead and subcontract ---------------------------------------- #
    std_overhead_cost = quantize_money(standard["overhead_cost"] * scale)
    actual_overhead_cost = (
        quantize_money(payload.overhead_absorbed)
        if payload.overhead_absorbed is not None
        else std_overhead_cost
    )
    overhead_var = quantize_money(actual_overhead_cost - std_overhead_cost)

    std_subcontract = quantize_money(_standard_subcontract_per_garment(sheet) * scale)
    actual_subcontract = (
        actual_subcontract_cost(session, order.id)
        if payload.include_subcontract
        else _ZERO
    )
    subcontract_var = quantize_money(actual_subcontract - std_subcontract)

    std_total = quantize_money(
        material.std_cost + std_labour_cost + std_overhead_cost + std_subcontract
    )
    actual_total = quantize_money(
        actual_material_cost
        + actual_labour_cost
        + actual_overhead_cost
        + actual_subcontract
    )

    run = ActualCostRun(
        run_number=next_document_number(session, "ACTUAL_COST_RUN", "ACR"),
        sales_order_id=order.id,
        cost_sheet_version_id=sheet.id,
        status=ActualCostRunStatus.draft,
        currency=sheet.currency,
        as_of=payload.as_of or date.today(),
        produced_qty=qty,
        std_material_cost=material.std_cost,
        std_labour_cost=std_labour_cost,
        std_overhead_cost=std_overhead_cost,
        std_total_cost=std_total,
        actual_material_cost=actual_material_cost,
        actual_labour_cost=actual_labour_cost,
        actual_overhead_cost=actual_overhead_cost,
        actual_subcontract_cost=actual_subcontract,
        actual_total_cost=actual_total,
        std_material_qty=material.std_qty,
        actual_material_qty=material.actual_qty,
        std_minutes=std_minutes,
        actual_minutes=act_minutes,
        std_rate_per_min=std_rate,
        actual_rate_per_min=actual_rate,
        notes=payload.notes,
    )
    session.add(run)
    session.flush()

    material_note = "; ".join(dict.fromkeys(material.notes)) or None
    rows = (
        (
            VarianceType.material_price,
            quantize_money(material.actual_qty * material.std_rate),
            actual_material_cost,
            material.price_variance,
            None,
        ),
        (
            VarianceType.material_usage,
            material.std_cost,
            quantize_money(material.actual_qty * material.std_rate),
            material.usage_variance,
            material_note,
        ),
        (
            VarianceType.labour_rate,
            quantize_money(std_rate * act_minutes),
            actual_labour_cost,
            labour_rate_var,
            None,
        ),
        (
            VarianceType.labour_efficiency,
            std_labour_cost,
            quantize_money(std_rate * act_minutes),
            labour_efficiency_var,
            None,
        ),
        (
            VarianceType.overhead,
            std_overhead_cost,
            actual_overhead_cost,
            overhead_var,
            None,
        ),
        (
            VarianceType.subcontract,
            std_subcontract,
            actual_subcontract,
            subcontract_var,
            None,
        ),
    )
    for variance_type, std_amount, act_amount, amount, note in rows:
        session.add(
            CostVariance(
                run_id=run.id,
                variance_type=variance_type,
                standard_amount=std_amount,
                actual_amount=act_amount,
                amount=amount,
                explanation=note,
            )
        )
    session.flush()
    session.refresh(run)
    return run


def total_variance(run: ActualCostRun) -> Decimal:
    return quantize_money(run.actual_total_cost - run.std_total_cost)


# --------------------------------------------------------------------------- #
# Posting variances to the general ledger
# --------------------------------------------------------------------------- #
_VARIANCE_ACCOUNTS = {
    VarianceType.material_price: "ACC_MATERIAL_PRICE_VARIANCE",
    VarianceType.material_usage: "ACC_MATERIAL_USAGE_VARIANCE",
    VarianceType.labour_rate: "ACC_LABOUR_RATE_VARIANCE",
    VarianceType.labour_efficiency: "ACC_LABOUR_EFFICIENCY_VARIANCE",
    VarianceType.overhead: "ACC_OVERHEAD_VARIANCE",
    VarianceType.subcontract: "ACC_SUBCONTRACT_VARIANCE",
}


def post_variance_journal(
    session: Session, run: ActualCostRun, actor: str
) -> ActualCostRun:
    """Reclassify a run's variances out of COGS into named variance accounts.

    This is deliberately **not** a second charge. Inventory in this system is
    relieved at actual issued cost and COGS is posted from that same valuation
    on dispatch, so the money is already in the ledger — it is simply sitting in
    one undifferentiated COGS line. Posting a run moves each variance to its own
    account and credits COGS by the same total, leaving profit unchanged while
    making the composition of the gap visible on the P&L.

    A run can only be posted once; its journal id is recorded on the run.
    """
    from ..finance import service as finance_service
    from ..finance.models import JournalSource

    if run.status == ActualCostRunStatus.posted:
        raise CostingError("This actual cost run has already been posted")

    lines: List[Tuple[str, Decimal, Decimal, Optional[str]]] = []
    net = _ZERO
    for variance in run.variances:
        amount = quantize_money(variance.amount)
        if amount == 0:
            continue
        code = getattr(finance_service, _VARIANCE_ACCOUNTS[variance.variance_type])
        label = variance.variance_type.value.replace("_", " ").capitalize()
        if amount > 0:
            lines.append((code, amount, _ZERO, label))
        else:
            lines.append((code, _ZERO, -amount, label))
        net += amount

    if not lines or net == 0:
        raise CostingError("This run has no variance to post")

    # Offsetting COGS leg keeps total cost of sales unchanged.
    if net > 0:
        lines.append((finance_service.ACC_COGS, _ZERO, net, "Variance reclassified from COGS"))
    else:
        lines.append((finance_service.ACC_COGS, -net, _ZERO, "Variance reclassified to COGS"))

    entry = finance_service.post_journal(
        session,
        lines=lines,
        memo=f"Cost variances {run.run_number}",
        source=JournalSource.system,
        reference_type="actual_cost_run",
        reference_id=run.id,
        entry_date=run.as_of,
    )
    run.status = ActualCostRunStatus.posted
    run.journal_entry_id = entry.id
    run.posted_at = utcnow().isoformat()
    run.posted_by = actor
    session.add(run)
    session.flush()
    return run
