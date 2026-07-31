from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..inventory.models import Roll
from ..inventory.service import balance, roll_available_qty
from ..kernel.query import page_bounds
from ..kernel.rbac import Role, require_roles
from ..masters.models import Material
from .models import (
    Bin,
    BinCreate,
    BinRead,
    BinType,
    ScanRequest,
    ScanResult,
    TaskAssign,
    TaskComplete,
    TaskCreate,
    TaskRead,
    TaskStatus,
    TaskType,
    WarehouseTask,
)
from .service import WmsError, complete_task, create_task, resolve_scan

router = APIRouter(prefix="/wms", tags=["wms"])


# --------------------------------------------------------------------------- #
# Bins
# --------------------------------------------------------------------------- #
@router.post("/bins", response_model=BinRead, status_code=201)
def create_bin(
    payload: BinCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.stores)),
):
    code = payload.code.strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="Bin code is required")
    existing = session.exec(
        select(Bin).where(Bin.code == code, Bin.warehouse == payload.warehouse)
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="Bin code already exists in this warehouse"
        )
    row = Bin(**{**payload.model_dump(), "code": code})
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.get("/bins", response_model=List[BinRead])
def list_bins(
    session: Session = Depends(get_session),
    warehouse: Optional[str] = None,
    zone: Optional[str] = None,
    bin_type: Optional[BinType] = None,
):
    stmt = select(Bin).where(Bin.active == True)  # noqa: E712
    if warehouse is not None:
        stmt = stmt.where(Bin.warehouse == warehouse)
    if zone is not None:
        stmt = stmt.where(Bin.zone == zone)
    if bin_type is not None:
        stmt = stmt.where(Bin.bin_type == bin_type)
    return session.exec(stmt.order_by(Bin.pick_sequence, Bin.code)).all()


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
class _TaskContext:
    """Batch-loaded lookups for rendering many tasks.

    Rendering a task needs its material, roll and two bins. Fetching those per
    task is four round trips per row; against a remote database that dominates
    the response. Built once per request instead.
    """

    __slots__ = ("materials", "rolls", "bins")

    def __init__(self, session: Session, tasks):
        tasks = list(tasks)
        self.materials = _by_id(session, Material, (t.material_id for t in tasks))
        self.rolls = _by_id(session, Roll, (t.roll_id for t in tasks))
        self.bins = _by_id(
            session, Bin,
            [t.from_bin_id for t in tasks] + [t.to_bin_id for t in tasks],
        )


def _by_id(session: Session, model, ids) -> dict:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {r.id: r for r in session.exec(select(model).where(model.id.in_(ids))).all()}


def _task_read(
    session: Session, task: WarehouseTask, ctx: Optional[_TaskContext] = None
) -> TaskRead:
    if ctx is not None:
        material = ctx.materials.get(task.material_id)
        roll = ctx.rolls.get(task.roll_id) if task.roll_id else None
        from_bin = ctx.bins.get(task.from_bin_id) if task.from_bin_id else None
        to_bin = ctx.bins.get(task.to_bin_id) if task.to_bin_id else None
    else:
        material = session.get(Material, task.material_id)
        roll = session.get(Roll, task.roll_id) if task.roll_id else None
        from_bin = session.get(Bin, task.from_bin_id) if task.from_bin_id else None
        to_bin = session.get(Bin, task.to_bin_id) if task.to_bin_id else None
    return TaskRead(
        id=task.id,
        task_number=task.task_number,
        task_type=task.task_type,
        status=task.status,
        priority=task.priority,
        material_id=task.material_id,
        material_code=material.code if material else None,
        material_name=material.name if material else None,
        roll_id=task.roll_id,
        roll_number=roll.roll_number if roll else None,
        quantity=task.quantity,
        from_bin_id=task.from_bin_id,
        from_bin_code=from_bin.code if from_bin else None,
        to_bin_id=task.to_bin_id,
        to_bin_code=to_bin.code if to_bin else None,
        reference_type=task.reference_type,
        reference_id=task.reference_id,
        assigned_to=task.assigned_to,
        due_date=task.due_date,
        completed_qty=task.completed_qty,
        completed_at=task.completed_at,
        completed_by=task.completed_by,
        short_reason=task.short_reason,
        warehouse_operation_id=task.warehouse_operation_id,
        notes=task.notes,
    )


@router.post("/tasks", response_model=TaskRead, status_code=201)
def create_warehouse_task(
    payload: TaskCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.stores, Role.planner)),
):
    try:
        task = create_task(session, payload)
    except WmsError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(task)
    return _task_read(session, task)


@router.get("/tasks", response_model=List[TaskRead])
def list_tasks(
    session: Session = Depends(get_session),
    status: Optional[TaskStatus] = None,
    task_type: Optional[TaskType] = None,
    assigned_to: Optional[str] = None,
    offset: int = 0,
    limit: int = Query(default=100),
):
    """Open work, ordered the way it should be walked.

    Sorting by priority then the destination bin's pick sequence turns a task
    list into a route rather than a set of unrelated errands.
    """
    offset, limit = page_bounds(offset, limit)
    stmt = select(WarehouseTask)
    if status is not None:
        stmt = stmt.where(WarehouseTask.status == status)
    if task_type is not None:
        stmt = stmt.where(WarehouseTask.task_type == task_type)
    if assigned_to is not None:
        stmt = stmt.where(WarehouseTask.assigned_to == assigned_to)
    tasks = session.exec(
        stmt.order_by(WarehouseTask.priority, WarehouseTask.id).offset(offset).limit(limit)
    ).all()

    ctx = _TaskContext(session, tasks)

    def route_key(task: WarehouseTask):
        # Reads from the batch-loaded bins. This used to issue a query inside
        # the sort comparator, which runs O(n log n) times per request.
        source = ctx.bins.get(task.from_bin_id) if task.from_bin_id else None
        return (task.priority, source.pick_sequence if source else 0, task.id)

    return [_task_read(session, t, ctx) for t in sorted(tasks, key=route_key)]


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: int, session: Session = Depends(get_session)):
    task = session.get(WarehouseTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_read(session, task)


@router.post("/tasks/{task_id}/assign", response_model=TaskRead)
def assign_task(
    task_id: int,
    payload: TaskAssign,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.stores, Role.planner)),
):
    task = session.get(WarehouseTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in (TaskStatus.completed, TaskStatus.cancelled):
        raise HTTPException(status_code=409, detail=f"Task is {task.status.value}")
    task.assigned_to = payload.assigned_to
    task.status = TaskStatus.assigned
    session.add(task)
    session.commit()
    session.refresh(task)
    return _task_read(session, task)


@router.post("/tasks/{task_id}/complete", response_model=TaskRead)
def complete_warehouse_task(
    task_id: int,
    payload: TaskComplete,
    session: Session = Depends(get_session),
    actor: str = Depends(require_roles(Role.stores)),
):
    """Confirm the work. Safe to retry with the same ``client_key``."""
    task = session.get(WarehouseTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        complete_task(session, task, payload, actor)
    except WmsError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(task)
    return _task_read(session, task)


@router.post("/tasks/{task_id}/cancel", response_model=TaskRead)
def cancel_task(
    task_id: int,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.stores, Role.planner)),
):
    task = session.get(WarehouseTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status == TaskStatus.completed:
        raise HTTPException(status_code=409, detail="Cannot cancel a completed task")
    task.status = TaskStatus.cancelled
    session.add(task)
    session.commit()
    session.refresh(task)
    return _task_read(session, task)


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
@router.post("/scan", response_model=ScanResult)
def scan(payload: ScanRequest, session: Session = Depends(get_session)):
    """Resolve a scanned barcode into whatever it identifies."""
    try:
        kind, obj = resolve_scan(session, payload.code)
    except WmsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if kind == "unknown":
        return ScanResult(kind="unknown", code=payload.code)

    if kind == "roll":
        material = session.get(Material, obj.material_id)
        tasks = session.exec(
            select(WarehouseTask).where(
                WarehouseTask.roll_id == obj.id,
                WarehouseTask.status.in_([TaskStatus.open, TaskStatus.assigned]),
            )
        ).all()
        ctx = _TaskContext(session, tasks)
        return ScanResult(
            kind="roll",
            id=obj.id,
            code=obj.roll_number,
            label=f"Roll {obj.roll_number}",
            material_id=obj.material_id,
            material_code=material.code if material else None,
            material_name=material.name if material else None,
            quantity=roll_available_qty(session, obj),
            uom=material.base_uom if material else None,
            status=obj.status.value,
            grade=obj.grade.value if getattr(obj, "grade", None) else None,
            open_tasks=[_task_read(session, t, ctx) for t in tasks],
        )

    if kind == "bin":
        tasks = session.exec(
            select(WarehouseTask).where(
                WarehouseTask.from_bin_id == obj.id,
                WarehouseTask.status.in_([TaskStatus.open, TaskStatus.assigned]),
            )
        ).all()
        ctx = _TaskContext(session, tasks)
        return ScanResult(
            kind="bin",
            id=obj.id,
            code=obj.code,
            label=f"{obj.warehouse} · {obj.code}",
            warehouse=obj.warehouse,
            location=obj.code,
            status=obj.bin_type.value,
            open_tasks=[_task_read(session, t, ctx) for t in tasks],
        )

    # material
    return ScanResult(
        kind="material",
        id=obj.id,
        code=obj.code,
        label=obj.name,
        material_id=obj.id,
        material_code=obj.code,
        material_name=obj.name,
        quantity=balance(session, material_id=obj.id),
        uom=obj.base_uom,
    )
