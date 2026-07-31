"""MES shift logs with OEE, and mobile WMS bins and directed tasks

Revision ID: d1f5b8c3e097
Revises: c9e4a7b2d135
Create Date: 2026-07-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d1f5b8c3e097"
down_revision: Union[str, Sequence[str], None] = "c9e4a7b2d135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


downtime_category = sa.Enum(
    "breakdown", "changeover", "material_shortage", "no_operator",
    "quality_issue", "power", "planned_maintenance", "other",
    name="downtimecategory",
)
shift_log_status = sa.Enum("open", "closed", name="shiftlogstatus")
bin_type = sa.Enum(
    "storage", "staging", "receiving", "shipping", "quarantine", name="bintype"
)
task_type = sa.Enum(
    "put_away", "pick", "replenish", "count", "transfer", name="tasktype"
)
task_status = sa.Enum(
    "open", "assigned", "completed", "cancelled", name="taskstatus"
)

_QTY = sa.Numeric(precision=18, scale=4)
_RATE = sa.Numeric(precision=18, scale=6)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (downtime_category, shift_log_status, bin_type, task_type, task_status):
        enum.create(bind, checkfirst=True)

    # ------------------------------------------------------------------ MES -- #
    op.create_table(
        "machine",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("work_centre_id", sa.Integer(), nullable=False),
        sa.Column("ideal_cycle_seconds", _RATE, nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["work_centre_id"], ["work_centre.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_machine_code"),
    )
    op.create_index("ix_machine_code", "machine", ["code"])
    op.create_index("ix_machine_work_centre_id", "machine", ["work_centre_id"])
    op.create_index("ix_machine_active", "machine", ["active"])

    op.create_table(
        "downtime_reason",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("category", downtime_category, nullable=False),
        sa.Column("planned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_downtime_reason_code"),
    )
    for column in ("code", "category", "planned", "active"):
        op.create_index(f"ix_downtime_reason_{column}", "downtime_reason", [column])

    op.create_table(
        "shift_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("log_number", sa.String(), nullable=False),
        sa.Column("work_centre_id", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=True),
        sa.Column("sewing_order_id", sa.Integer(), nullable=True),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("shift", sa.String(length=16), nullable=False, server_default="A"),
        sa.Column("operators", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("planned_minutes", _QTY, nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("good_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reject_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ideal_cycle_seconds", _RATE, nullable=True),
        sa.Column("status", shift_log_status, nullable=False, server_default="open"),
        sa.Column("planned_downtime_minutes", _QTY, nullable=False, server_default="0"),
        sa.Column("unplanned_downtime_minutes", _QTY, nullable=False, server_default="0"),
        sa.Column("run_minutes", _QTY, nullable=False, server_default="0"),
        sa.Column("availability_pct", _RATE, nullable=False, server_default="0"),
        sa.Column("performance_pct", _RATE, nullable=False, server_default="0"),
        sa.Column("quality_pct", _RATE, nullable=False, server_default="0"),
        sa.Column("oee_pct", _RATE, nullable=False, server_default="0"),
        sa.Column("performance_capped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("closed_at", sa.String(), nullable=True),
        sa.Column("closed_by", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["work_centre_id"], ["work_centre.id"]),
        sa.ForeignKeyConstraint(["machine_id"], ["machine.id"]),
        sa.ForeignKeyConstraint(["sewing_order_id"], ["sewing_order.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("log_number", name="uq_shift_log_number"),
    )
    for column in (
        "log_number", "work_centre_id", "machine_id", "sewing_order_id",
        "log_date", "shift", "status",
    ):
        op.create_index(f"ix_shift_log_{column}", "shift_log", [column])

    op.create_table(
        "downtime_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shift_log_id", sa.Integer(), nullable=False),
        sa.Column("reason_id", sa.Integer(), nullable=False),
        sa.Column("minutes", _QTY, nullable=False, server_default="0"),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("recorded_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["shift_log_id"], ["shift_log.id"]),
        sa.ForeignKeyConstraint(["reason_id"], ["downtime_reason.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_downtime_event_shift_log_id", "downtime_event", ["shift_log_id"])
    op.create_index("ix_downtime_event_reason_id", "downtime_event", ["reason_id"])

    # ------------------------------------------------------------------ WMS -- #
    op.create_table(
        "bin",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("warehouse", sa.String(length=32), nullable=False, server_default="MAIN"),
        sa.Column("zone", sa.String(length=32), nullable=True),
        sa.Column("bin_type", bin_type, nullable=False, server_default="storage"),
        sa.Column("pick_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("capacity_qty", _QTY, nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("warehouse", "code", name="uq_bin_warehouse_code"),
    )
    for column in ("code", "warehouse", "zone", "bin_type", "pick_sequence", "active"):
        op.create_index(f"ix_bin_{column}", "bin", [column])

    op.create_table(
        "warehouse_task",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_number", sa.String(), nullable=False),
        sa.Column("task_type", task_type, nullable=False),
        sa.Column("status", task_status, nullable=False, server_default="open"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("roll_id", sa.Integer(), nullable=True),
        sa.Column("quantity", _QTY, nullable=False, server_default="0"),
        sa.Column("from_bin_id", sa.Integer(), nullable=True),
        sa.Column("to_bin_id", sa.Integer(), nullable=True),
        sa.Column("reference_type", sa.String(), nullable=True),
        sa.Column("reference_id", sa.Integer(), nullable=True),
        sa.Column("assigned_to", sa.String(length=320), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_qty", _QTY, nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_by", sa.String(), nullable=True),
        sa.Column("short_reason", sa.String(length=200), nullable=True),
        sa.Column("client_key", sa.String(length=100), nullable=True),
        sa.Column("warehouse_operation_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["material_id"], ["material.id"]),
        sa.ForeignKeyConstraint(["roll_id"], ["roll.id"]),
        sa.ForeignKeyConstraint(["from_bin_id"], ["bin.id"]),
        sa.ForeignKeyConstraint(["to_bin_id"], ["bin.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_number", name="uq_warehouse_task_number"),
        sa.UniqueConstraint("client_key", name="uq_warehouse_task_client_key"),
    )
    for column in (
        "task_number", "task_type", "status", "priority", "material_id", "roll_id",
        "from_bin_id", "to_bin_id", "reference_type", "reference_id",
        "assigned_to", "due_date", "client_key", "warehouse_operation_id",
    ):
        op.create_index(f"ix_warehouse_task_{column}", "warehouse_task", [column])


def downgrade() -> None:
    op.drop_table("warehouse_task")
    op.drop_table("bin")
    op.drop_table("downtime_event")
    op.drop_table("shift_log")
    op.drop_table("downtime_reason")
    op.drop_table("machine")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in (
            task_status, task_type, bin_type, shift_log_status, downtime_category,
        ):
            enum.drop(bind, checkfirst=True)
