"""Time & Action endpoints: templates, per-order plans, and the late board."""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.rbac import Role, require_roles
from ..sales.models import SalesOrder, SalesOrderStatus
from .models import (
    TnaApplyRequest,
    TnaMilestone,
    TnaMilestoneRead,
    TnaMilestoneUpdate,
    TnaTemplate,
    TnaTemplateCreate,
    TnaTemplateRead,
    TnaTemplateStep,
    TnaTemplateStepRead,
    TnaOrderBoardRow,
)
from .service import apply_template, milestone_read, milestone_status, seed_default_template
from .models import TnaMilestoneStatus

router = APIRouter(prefix="/tna", tags=["tna"])


def _template_read(template: TnaTemplate) -> TnaTemplateRead:
    return TnaTemplateRead(
        id=template.id,
        name=template.name,
        active=template.active,
        steps=[
            TnaTemplateStepRead(
                id=step.id,
                sequence=step.sequence,
                name=step.name,
                offset_days=step.offset_days,
                owner_role=step.owner_role,
            )
            for step in template.steps
        ],
    )


@router.get("/templates", response_model=List[TnaTemplateRead])
def list_templates(session: Session = Depends(get_session)):
    # The standard ladder is always available without setup.
    seed_default_template(session)
    session.commit()
    templates = session.exec(
        select(TnaTemplate)
        .options(selectinload(TnaTemplate.steps))
        .where(TnaTemplate.active == True)  # noqa: E712
        .order_by(TnaTemplate.id)
    ).all()
    return [_template_read(template) for template in templates]


@router.post(
    "/templates",
    response_model=TnaTemplateRead,
    status_code=201,
    dependencies=[Depends(require_roles(Role.merchandiser))],
)
def create_template(payload: TnaTemplateCreate, session: Session = Depends(get_session)):
    if session.exec(select(TnaTemplate).where(TnaTemplate.name == payload.name)).first():
        raise HTTPException(status_code=409, detail="A template with this name already exists")
    template = TnaTemplate(name=payload.name)
    session.add(template)
    session.flush()
    for step in payload.steps:
        session.add(
            TnaTemplateStep(
                template_id=template.id,
                sequence=step.sequence,
                name=step.name,
                offset_days=step.offset_days,
                owner_role=step.owner_role,
            )
        )
    session.commit()
    session.refresh(template)
    return _template_read(template)


@router.post(
    "/orders/{order_id}/apply",
    response_model=List[TnaMilestoneRead],
    status_code=201,
    dependencies=[Depends(require_roles(Role.merchandiser, Role.planner))],
)
def apply(order_id: int, payload: TnaApplyRequest, session: Session = Depends(get_session)):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    if order.status in (SalesOrderStatus.cancelled,):
        raise HTTPException(status_code=422, detail="Cannot plan a cancelled order")
    template = session.get(TnaTemplate, payload.template_id)
    if template is None or not template.active:
        raise HTTPException(status_code=404, detail="Template not found")
    try:
        milestones = apply_template(
            session,
            sales_order_id=order_id,
            template=template,
            ex_factory_date=payload.ex_factory_date,
            replace=payload.replace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    for milestone in milestones:
        session.refresh(milestone)
    return [milestone_read(m) for m in milestones]


@router.get("/orders/{order_id}", response_model=List[TnaMilestoneRead])
def order_plan(order_id: int, session: Session = Depends(get_session)):
    if session.get(SalesOrder, order_id) is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    milestones = session.exec(
        select(TnaMilestone)
        .where(TnaMilestone.sales_order_id == order_id)
        .order_by(TnaMilestone.sequence)
    ).all()
    return [milestone_read(m) for m in milestones]


@router.patch(
    "/milestones/{milestone_id}",
    response_model=TnaMilestoneRead,
    dependencies=[Depends(require_roles(
        Role.merchandiser, Role.planner, Role.stores,
        Role.cutting_supervisor, Role.sewing_supervisor, Role.quality_inspector,
    ))],
)
def update_milestone(
    milestone_id: int,
    payload: TnaMilestoneUpdate,
    session: Session = Depends(get_session),
):
    milestone = session.get(TnaMilestone, milestone_id)
    if milestone is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    if payload.planned_date is not None:
        milestone.planned_date = payload.planned_date
    if payload.clear_actual:
        milestone.actual_date = None
    elif payload.actual_date is not None:
        milestone.actual_date = payload.actual_date
    if payload.notes is not None:
        milestone.notes = payload.notes.strip()[:500] or None
    session.add(milestone)
    session.commit()
    session.refresh(milestone)
    return milestone_read(milestone)


@router.get("/board", response_model=List[TnaOrderBoardRow])
def board(
    late_only: bool = False,
    session: Session = Depends(get_session),
):
    """Cross-order execution board: lateness at a glance, worst first."""
    open_statuses = (
        SalesOrderStatus.confirmed,
        SalesOrderStatus.in_production,
        SalesOrderStatus.partially_shipped,
    )
    orders = session.exec(
        select(SalesOrder).where(SalesOrder.status.in_(open_statuses))
    ).all()
    order_ids = [order.id for order in orders]
    milestones: dict[int, List[TnaMilestone]] = {}
    if order_ids:
        for milestone in session.exec(
            select(TnaMilestone)
            .where(TnaMilestone.sales_order_id.in_(order_ids))
            .order_by(TnaMilestone.sequence)
        ):
            milestones.setdefault(milestone.sales_order_id, []).append(milestone)

    rows: List[TnaOrderBoardRow] = []
    for order in orders:
        plan = milestones.get(order.id, [])
        if not plan:
            continue
        late = 0
        completed = 0
        worst = 0
        next_milestone: Optional[TnaMilestone] = None
        for milestone in plan:
            status, slip = milestone_status(milestone)
            if status in (TnaMilestoneStatus.done, TnaMilestoneStatus.done_late):
                completed += 1
            else:
                if status is TnaMilestoneStatus.late:
                    late += 1
                if next_milestone is None:
                    next_milestone = milestone
            worst = max(worst, slip if milestone.actual_date is None else 0)
        row = TnaOrderBoardRow(
            sales_order_id=order.id,
            order_number=order.order_number,
            customer_id=order.customer_id,
            order_status=order.status.value,
            ex_factory_date=max((m.planned_date for m in plan), default=None),
            total_milestones=len(plan),
            completed=completed,
            late=late,
            worst_slip_days=worst,
            next_milestone=next_milestone.name if next_milestone else None,
            next_planned_date=next_milestone.planned_date if next_milestone else None,
        )
        if not late_only or row.late > 0:
            rows.append(row)
    rows.sort(key=lambda r: (-r.worst_slip_days, r.next_planned_date or date.max))
    return rows
