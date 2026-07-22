from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.rbac import Role, require_roles
from ..kernel.types import quantize_money
from ..sales.models import SalesOrder
from ..styles.models import Style
from .models import (
    CostLineRead,
    CostSheetCreate,
    CostSheetRead,
    CostSheetStatus,
    CostSheetVersion,
    OrderProfitabilityRead,
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
