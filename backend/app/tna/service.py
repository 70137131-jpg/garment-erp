"""T&A domain logic: template application and derived milestone status."""

from datetime import date, timedelta
from typing import List, Optional

from sqlmodel import Session, select

from .models import (
    TnaMilestone,
    TnaMilestoneRead,
    TnaMilestoneStatus,
    TnaTemplate,
    TnaTemplateStep,
)

DUE_SOON_DAYS = 3

# The standard export-order ladder, offsets in days from ex-factory.
DEFAULT_TEMPLATE_NAME = "Standard export order"
DEFAULT_TEMPLATE_STEPS: List[tuple[int, str, int, Optional[str]]] = [
    (10, "Order confirmed", -75, "merchandiser"),
    (20, "Fabric booked", -70, "procurement"),
    (30, "Lab dips approved", -60, "quality_inspector"),
    (40, "Fabric in-house", -45, "stores"),
    (50, "Trims in-house", -40, "stores"),
    (60, "PP sample approved", -35, "merchandiser"),
    (70, "Cutting started", -30, "cutting_supervisor"),
    (80, "Sewing started", -25, "sewing_supervisor"),
    (90, "Sewing completed", -10, "sewing_supervisor"),
    (100, "Finishing completed", -8, "sewing_supervisor"),
    (110, "Final inspection passed", -5, "quality_inspector"),
    (120, "Packing completed", -3, "stores"),
    (130, "Ex-factory", 0, "merchandiser"),
]


def seed_default_template(session: Session) -> TnaTemplate:
    """Idempotently ensure the standard template exists."""
    existing = session.exec(
        select(TnaTemplate).where(TnaTemplate.name == DEFAULT_TEMPLATE_NAME)
    ).first()
    if existing is not None:
        return existing
    template = TnaTemplate(name=DEFAULT_TEMPLATE_NAME)
    session.add(template)
    session.flush()
    for sequence, name, offset, owner in DEFAULT_TEMPLATE_STEPS:
        session.add(
            TnaTemplateStep(
                template_id=template.id,
                sequence=sequence,
                name=name,
                offset_days=offset,
                owner_role=owner,
            )
        )
    return template


def apply_template(
    session: Session,
    *,
    sales_order_id: int,
    template: TnaTemplate,
    ex_factory_date: date,
    replace: bool,
) -> List[TnaMilestone]:
    existing = session.exec(
        select(TnaMilestone).where(TnaMilestone.sales_order_id == sales_order_id)
    ).all()
    if existing and not replace:
        raise ValueError(
            "This order already has a T&A plan; pass replace=true to rebuild it"
        )
    for milestone in existing:
        session.delete(milestone)
    created: List[TnaMilestone] = []
    for step in template.steps:
        milestone = TnaMilestone(
            sales_order_id=sales_order_id,
            sequence=step.sequence,
            name=step.name,
            planned_date=ex_factory_date + timedelta(days=step.offset_days),
            owner_role=step.owner_role,
        )
        session.add(milestone)
        created.append(milestone)
    return created


def milestone_status(milestone: TnaMilestone, today: Optional[date] = None) -> tuple[TnaMilestoneStatus, int]:
    """(status, slip_days). slip_days > 0 means late by that many days."""
    today = today or date.today()
    if milestone.actual_date is not None:
        slip = (milestone.actual_date - milestone.planned_date).days
        if slip > 0:
            return TnaMilestoneStatus.done_late, slip
        return TnaMilestoneStatus.done, 0
    if milestone.planned_date < today:
        return TnaMilestoneStatus.late, (today - milestone.planned_date).days
    if milestone.planned_date <= today + timedelta(days=DUE_SOON_DAYS):
        return TnaMilestoneStatus.due_soon, 0
    return TnaMilestoneStatus.pending, 0


def milestone_read(milestone: TnaMilestone, today: Optional[date] = None) -> TnaMilestoneRead:
    status, slip = milestone_status(milestone, today)
    return TnaMilestoneRead(
        id=milestone.id,
        sales_order_id=milestone.sales_order_id,
        sequence=milestone.sequence,
        name=milestone.name,
        planned_date=milestone.planned_date,
        actual_date=milestone.actual_date,
        owner_role=milestone.owner_role,
        notes=milestone.notes,
        status=status,
        slip_days=slip,
    )
