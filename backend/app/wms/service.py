"""13.1 — task lifecycle and scan resolution.

Task completion is the only interesting part. It must:

1. be **idempotent** — a handheld retry replays the original result rather than
   moving stock twice;
2. move stock **through the ledger**, never around it, so balances stay derived
   from movements;
3. tolerate a **short pick** — the operator found less than the task directed.
   That is a fact about the warehouse, not an error, so it is recorded with a
   reason rather than rejected.
"""

from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from ..inventory.models import Roll, WarehouseOperationType
from ..inventory.service import StockError, move_stock
from ..kernel.audit import utcnow
from ..kernel.numbering import next_document_number
from ..kernel.types import quantize_qty
from ..masters.models import Material
from .models import Bin, TaskComplete, TaskCreate, TaskStatus, TaskType, WarehouseTask

_ZERO = Decimal("0")

# Task types that physically relocate stock when completed. A count does not.
_MOVING_TASKS = (TaskType.put_away, TaskType.pick, TaskType.replenish, TaskType.transfer)


class WmsError(Exception):
    """Domain error creating or completing warehouse work."""


def create_task(session: Session, payload: TaskCreate) -> WarehouseTask:
    if payload.quantity <= 0:
        raise WmsError("Task quantity must be positive")
    if session.get(Material, payload.material_id) is None:
        raise WmsError("Material not found")

    for bin_id in (payload.from_bin_id, payload.to_bin_id):
        if bin_id is not None and session.get(Bin, bin_id) is None:
            raise WmsError("Bin not found")

    if payload.task_type in _MOVING_TASKS and payload.from_bin_id is None:
        raise WmsError(f"A {payload.task_type.value} task needs a source bin")
    if payload.task_type in _MOVING_TASKS and payload.to_bin_id is None:
        raise WmsError(f"A {payload.task_type.value} task needs a destination bin")

    if payload.roll_id is not None:
        roll = session.get(Roll, payload.roll_id)
        if roll is None:
            raise WmsError("Roll not found")
        if roll.material_id != payload.material_id:
            raise WmsError("Roll does not belong to this material")

    task = WarehouseTask(
        task_number=next_document_number(session, "WAREHOUSE_TASK", "TASK"),
        task_type=payload.task_type,
        status=TaskStatus.assigned if payload.assigned_to else TaskStatus.open,
        priority=payload.priority,
        material_id=payload.material_id,
        roll_id=payload.roll_id,
        quantity=quantize_qty(payload.quantity),
        from_bin_id=payload.from_bin_id,
        to_bin_id=payload.to_bin_id,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        assigned_to=payload.assigned_to,
        due_date=payload.due_date,
        notes=payload.notes,
    )
    session.add(task)
    session.flush()
    return task


def complete_task(
    session: Session, task: WarehouseTask, payload: TaskComplete, actor: str
) -> WarehouseTask:
    """Post the movement the task directed and close it.

    Replaying the same ``client_key`` against an already-completed task returns
    it untouched — the handheld's retry is a no-op rather than a second move.
    """
    if task.status == TaskStatus.completed:
        if payload.client_key and payload.client_key == task.client_key:
            return task
        raise WmsError("This task has already been completed")
    if task.status == TaskStatus.cancelled:
        raise WmsError("This task has been cancelled")

    if payload.client_key:
        clash = session.exec(
            select(WarehouseTask).where(
                WarehouseTask.client_key == payload.client_key,
                WarehouseTask.id != task.id,
            )
        ).first()
        if clash is not None:
            raise WmsError("This client key has already been used by another task")

    quantity = quantize_qty(
        payload.completed_qty if payload.completed_qty is not None else task.quantity
    )
    if quantity < 0:
        raise WmsError("Completed quantity cannot be negative")
    if quantity > task.quantity:
        raise WmsError("Completed quantity cannot exceed the directed quantity")
    if quantity < task.quantity and not payload.short_reason:
        raise WmsError("A short completion needs a reason")

    destination_id = payload.to_bin_id or task.to_bin_id
    operation_id: Optional[int] = None

    if quantity > 0 and task.task_type in _MOVING_TASKS:
        source = session.get(Bin, task.from_bin_id) if task.from_bin_id else None
        destination = session.get(Bin, destination_id) if destination_id else None
        if source is None or destination is None:
            raise WmsError("Both a source and a destination bin are required")
        try:
            operation = move_stock(
                session,
                material_id=task.material_id,
                quantity=quantity,
                actor=actor,
                from_warehouse=source.warehouse,
                from_location=source.code,
                to_warehouse=destination.warehouse,
                to_location=destination.code,
                roll_id=task.roll_id,
                operation_type=(
                    WarehouseOperationType.put_away
                    if task.task_type == TaskType.put_away
                    else WarehouseOperationType.transfer
                ),
                reference=task.task_number,
                reason=payload.short_reason,
            )
        except StockError as exc:
            raise WmsError(str(exc)) from exc
        operation_id = operation.id

    task.completed_qty = quantity
    task.to_bin_id = destination_id
    task.short_reason = payload.short_reason
    task.client_key = payload.client_key
    task.warehouse_operation_id = operation_id
    task.status = TaskStatus.completed
    task.completed_at = utcnow()
    task.completed_by = actor
    session.add(task)
    session.flush()
    return task


def resolve_scan(session: Session, code: str):
    """Identify a scanned code as a roll, a bin, or a material.

    Rolls are checked first because they are the most specific identity on the
    floor, and a roll scan answers the operator's real question — what is this,
    and what am I meant to do with it?
    """
    code = (code or "").strip()
    if not code:
        raise WmsError("Nothing was scanned")

    roll = session.exec(select(Roll).where(Roll.roll_number == code)).first()
    if roll is not None:
        return "roll", roll

    upper = code.upper()
    bin_row = session.exec(select(Bin).where(Bin.code == upper)).first()
    if bin_row is not None:
        return "bin", bin_row

    material = session.exec(select(Material).where(Material.code == upper)).first()
    if material is not None:
        return "material", material

    return "unknown", None
