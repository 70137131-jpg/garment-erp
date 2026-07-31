from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.query import csv_download, page_bounds
from ..kernel.rbac import Role, require_roles
from ..kernel.types import quantize_money
from ..sales.models import SalesOrder
from ..styles.models import Style
from .actuals import build_actual_cost_run, post_variance_journal, total_variance
from .models import (
    ActualCostRun,
    ActualCostRunCreate,
    ActualCostRunRead,
    ActualCostRunStatus,
    CostLineRead,
    CostSheetCreate,
    CostSheetRead,
    CostSheetStatus,
    CostSheetVersion,
    CostVariance,
    CostVarianceRead,
    OrderProfitabilityRead,
    VarianceType,
)
from .service import (
    CostingError,
    approve_cost_sheet,
    create_cost_sheet,
    current_cost_sheet,
    line_amount,
    rollup,
)

router = APIRouter(prefix="/costing", tags=["costing"])


def _read(sheet: CostSheetVersion) -> CostSheetRead:
    totals = rollup(sheet)
    return CostSheetRead(
        id=sheet.id,
        style_id=sheet.style_id,
        version_no=sheet.version_no,
        status=sheet.status,
        base_size=sheet.base_size,
        currency=sheet.currency,
        sam=sheet.sam,
        sewing_cost_per_min=sheet.sewing_cost_per_min,
        sewing_efficiency_pct=sheet.sewing_efficiency_pct,
        overhead_pct=sheet.overhead_pct,
        margin_pct=sheet.margin_pct,
        lines=[
            CostLineRead(
                id=l.id,
                category=l.category,
                description=l.description,
                material_id=l.material_id,
                quantity=l.quantity,
                rate=l.rate,
                wastage_pct=l.wastage_pct,
                amount=line_amount(l),
            )
            for l in sheet.lines
        ],
        **totals,
    )


@router.post("/styles/{style_id}/cost-sheets", response_model=CostSheetRead, status_code=201)
def create_sheet(
    style_id: int,
    payload: CostSheetCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser, Role.finance)),
):
    style = session.get(Style, style_id)
    if style is None:
        raise HTTPException(status_code=404, detail="Style not found")
    try:
        sheet = create_cost_sheet(session, style, payload)
        rollup(sheet)  # validate margin etc. before commit
    except CostingError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(sheet)
    return _read(sheet)


@router.get("/styles/{style_id}/cost-sheets", response_model=List[CostSheetRead])
def list_sheets(style_id: int, session: Session = Depends(get_session)):
    sheets = session.exec(
        select(CostSheetVersion)
        .where(CostSheetVersion.style_id == style_id)
        .order_by(CostSheetVersion.version_no)
    ).all()
    return [_read(s) for s in sheets]


@router.get("/cost-sheets/{sheet_id}", response_model=CostSheetRead)
def get_sheet(sheet_id: int, session: Session = Depends(get_session)):
    sheet = session.get(CostSheetVersion, sheet_id)
    if sheet is None:
        raise HTTPException(status_code=404, detail="Cost sheet not found")
    return _read(sheet)


@router.post("/cost-sheets/{sheet_id}/approve", response_model=CostSheetRead)
def approve_sheet(
    sheet_id: int,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.finance, Role.merchandiser)),
):
    sheet = session.get(CostSheetVersion, sheet_id)
    if sheet is None:
        raise HTTPException(status_code=404, detail="Cost sheet not found")
    if sheet.status != CostSheetStatus.draft:
        raise HTTPException(
            status_code=409, detail=f"Cannot approve a '{sheet.status.value}' cost sheet"
        )
    approve_cost_sheet(session, sheet, actor)
    session.commit()
    session.refresh(sheet)
    return _read(sheet)


# --------------------------------------------------------------------------- #
# Order profitability (6.7)
# --------------------------------------------------------------------------- #
@router.get(
    "/sales-orders/{order_id}/profitability",
    response_model=OrderProfitabilityRead,
)
def order_profitability(order_id: int, session: Session = Depends(get_session)):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")

    revenue = Decimal("0")
    total_cost = Decimal("0")
    total_qty = 0
    style_ids = set()
    single_sheet_id: Optional[int] = None

    for line in order.lines:
        qty = sum(c.confirmed_qty or c.ordered_qty for c in line.cells)
        total_qty += qty
        revenue += line.unit_price * Decimal(qty)
        style_ids.add(line.style_id)
        sheet = current_cost_sheet(session, line.style_id)
        if sheet is not None:
            single_sheet_id = sheet.id
            unit_cost = rollup(sheet)["total_cost"]
            total_cost += unit_cost * Decimal(qty)

    revenue = quantize_money(revenue)
    total_cost = quantize_money(total_cost)
    profit = quantize_money(revenue - total_cost)
    margin_pct = (
        quantize_money(profit / revenue * Decimal("100")) if revenue > 0 else Decimal("0")
    )
    unit_cost = quantize_money(total_cost / Decimal(total_qty)) if total_qty else Decimal("0")

    return OrderProfitabilityRead(
        sales_order_id=order.id,
        order_number=order.order_number,
        total_quantity=total_qty,
        revenue=revenue,
        unit_cost=unit_cost,
        total_cost=total_cost,
        profit=profit,
        margin_pct=margin_pct,
        cost_sheet_version_id=single_sheet_id if len(style_ids) == 1 else None,
    )


# --------------------------------------------------------------------------- #
# Actual costing and variance analysis (6.8)
# --------------------------------------------------------------------------- #
def _run_read(
    session: Session, run: ActualCostRun, orders: Optional[dict] = None
) -> ActualCostRunRead:
    order = (
        orders.get(run.sales_order_id)
        if orders is not None
        else session.get(SalesOrder, run.sales_order_id)
    )
    unit_std = (
        quantize_money(run.std_total_cost / Decimal(run.produced_qty))
        if run.produced_qty
        else Decimal("0")
    )
    unit_actual = (
        quantize_money(run.actual_total_cost / Decimal(run.produced_qty))
        if run.produced_qty
        else Decimal("0")
    )
    return ActualCostRunRead(
        id=run.id,
        run_number=run.run_number,
        sales_order_id=run.sales_order_id,
        order_number=order.order_number if order else None,
        cost_sheet_version_id=run.cost_sheet_version_id,
        status=run.status,
        currency=run.currency,
        as_of=run.as_of,
        produced_qty=run.produced_qty,
        std_material_cost=run.std_material_cost,
        std_labour_cost=run.std_labour_cost,
        std_overhead_cost=run.std_overhead_cost,
        std_total_cost=run.std_total_cost,
        actual_material_cost=run.actual_material_cost,
        actual_labour_cost=run.actual_labour_cost,
        actual_overhead_cost=run.actual_overhead_cost,
        actual_subcontract_cost=run.actual_subcontract_cost,
        actual_total_cost=run.actual_total_cost,
        std_material_qty=run.std_material_qty,
        actual_material_qty=run.actual_material_qty,
        std_minutes=run.std_minutes,
        actual_minutes=run.actual_minutes,
        std_rate_per_min=run.std_rate_per_min,
        actual_rate_per_min=run.actual_rate_per_min,
        total_variance=total_variance(run),
        unit_std_cost=unit_std,
        unit_actual_cost=unit_actual,
        journal_entry_id=run.journal_entry_id,
        posted_at=run.posted_at,
        posted_by=run.posted_by,
        notes=run.notes,
        variances=[
            CostVarianceRead(
                id=v.id,
                variance_type=v.variance_type,
                standard_amount=v.standard_amount,
                actual_amount=v.actual_amount,
                amount=v.amount,
                favourable=v.amount < 0,
                explanation=v.explanation,
            )
            for v in run.variances
        ],
    )


@router.post(
    "/sales-orders/{order_id}/actual-cost",
    response_model=ActualCostRunRead,
    status_code=201,
)
def create_actual_cost_run(
    order_id: int,
    payload: ActualCostRunCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.finance, Role.merchandiser)),
):
    """Freeze an actual-vs-standard analysis for one order."""
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    try:
        run = build_actual_cost_run(session, order, payload)
    except CostingError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(run)
    return _run_read(session, run)


@router.get(
    "/sales-orders/{order_id}/actual-cost", response_model=List[ActualCostRunRead]
)
def list_actual_cost_runs(order_id: int, session: Session = Depends(get_session)):
    runs = session.exec(
        select(ActualCostRun)
        .where(ActualCostRun.sales_order_id == order_id)
        .order_by(ActualCostRun.id.desc())
    ).all()
    # Every run on this endpoint shares one order; fetch it once.
    order = session.get(SalesOrder, order_id)
    orders = {order_id: order} if order else {}
    return [_run_read(session, r, orders) for r in runs]


@router.get("/actual-cost/{run_id}", response_model=ActualCostRunRead)
def get_actual_cost_run(run_id: int, session: Session = Depends(get_session)):
    run = session.get(ActualCostRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Actual cost run not found")
    return _run_read(session, run)


@router.post("/actual-cost/{run_id}/post", response_model=ActualCostRunRead)
def post_actual_cost_run(
    run_id: int,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.finance)),
):
    """Reclassify the run's variances out of COGS into variance accounts."""
    run = session.get(ActualCostRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Actual cost run not found")
    try:
        post_variance_journal(session, run, actor)
    except CostingError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # finance rejects unbalanced/unseeded postings
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(run)
    return _run_read(session, run)


@router.get("/variances", response_model=List[CostVarianceRead])
def list_variances(
    session: Session = Depends(get_session),
    variance_type: Optional[VarianceType] = None,
    sales_order_id: Optional[int] = None,
    status: Optional[ActualCostRunStatus] = None,
    adverse_only: bool = False,
    offset: int = 0,
    limit: int = Query(default=100),
):
    """Cross-order variance register."""
    offset, limit = page_bounds(offset, limit)
    stmt = select(CostVariance).join(
        ActualCostRun, ActualCostRun.id == CostVariance.run_id
    )
    if variance_type is not None:
        stmt = stmt.where(CostVariance.variance_type == variance_type)
    if sales_order_id is not None:
        stmt = stmt.where(ActualCostRun.sales_order_id == sales_order_id)
    if status is not None:
        stmt = stmt.where(ActualCostRun.status == status)
    if adverse_only:
        stmt = stmt.where(CostVariance.amount > 0)
    rows = session.exec(
        stmt.order_by(CostVariance.id.desc()).offset(offset).limit(limit)
    ).all()
    return [
        CostVarianceRead(
            id=v.id,
            variance_type=v.variance_type,
            standard_amount=v.standard_amount,
            actual_amount=v.actual_amount,
            amount=v.amount,
            favourable=v.amount < 0,
            explanation=v.explanation,
        )
        for v in rows
    ]


@router.get("/variances.csv")
def export_variances(
    session: Session = Depends(get_session),
    sales_order_id: Optional[int] = None,
):
    stmt = (
        select(CostVariance, ActualCostRun)
        .join(ActualCostRun, ActualCostRun.id == CostVariance.run_id)
        .order_by(CostVariance.id)
    )
    if sales_order_id is not None:
        stmt = stmt.where(ActualCostRun.sales_order_id == sales_order_id)
    rows = [
        {
            "run_number": run.run_number,
            "sales_order_id": run.sales_order_id,
            "as_of": run.as_of,
            "produced_qty": run.produced_qty,
            "variance_type": variance.variance_type.value,
            "standard_amount": variance.standard_amount,
            "actual_amount": variance.actual_amount,
            "amount": variance.amount,
            "direction": "favourable" if variance.amount < 0 else "adverse",
            "explanation": variance.explanation or "",
        }
        for variance, run in session.exec(stmt).all()
    ]
    return csv_download(
        "cost-variances.csv",
        columns=[
            ("run_number", "Run"),
            ("sales_order_id", "Sales order"),
            ("as_of", "As of"),
            ("produced_qty", "Produced qty"),
            ("variance_type", "Variance"),
            ("standard_amount", "Standard"),
            ("actual_amount", "Actual"),
            ("amount", "Amount"),
            ("direction", "Direction"),
            ("explanation", "Explanation"),
        ],
        rows=rows,
    )
