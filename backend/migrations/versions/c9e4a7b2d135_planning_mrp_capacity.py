"""planning: work centres, capacity bookings, MRP runs and planned orders

Revision ID: c9e4a7b2d135
Revises: b7d3e1f9c084
Create Date: 2026-07-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c9e4a7b2d135"
down_revision: Union[str, Sequence[str], None] = "b7d3e1f9c084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


work_centre_type = sa.Enum(
    "cutting", "sewing", "finishing", "packing", "embroidery", "washing",
    name="workcentretype",
)
booking_status = sa.Enum(
    "planned", "confirmed", "released", "cancelled", name="capacitybookingstatus"
)
mrp_run_status = sa.Enum("draft", "firmed", name="mrprunstatus")
planned_order_status = sa.Enum(
    "planned", "firmed", "cancelled", name="plannedorderstatus"
)

_QTY = sa.Numeric(precision=18, scale=4)
_RATE = sa.Numeric(precision=18, scale=6)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (work_centre_type, booking_status, mrp_run_status, planned_order_status):
        enum.create(bind, checkfirst=True)

    # ---- work centres ----------------------------------------------------- #
    op.create_table(
        "work_centre",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("centre_type", work_centre_type, nullable=False),
        sa.Column("operators", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shift_minutes", sa.Integer(), nullable=False, server_default="480"),
        sa.Column("shifts_per_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("efficiency_pct", _RATE, nullable=False, server_default="100"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_work_centre_code"),
    )
    op.create_index("ix_work_centre_code", "work_centre", ["code"])
    op.create_index("ix_work_centre_centre_type", "work_centre", ["centre_type"])
    op.create_index("ix_work_centre_active", "work_centre", ["active"])

    op.create_table(
        "capacity_exception",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_centre_id", sa.Integer(), nullable=False),
        sa.Column("exception_date", sa.Date(), nullable=False),
        sa.Column("available_minutes", _QTY, nullable=False, server_default="0"),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["work_centre_id"], ["work_centre.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_centre_id", "exception_date", name="uq_capacity_exception_day"
        ),
    )
    op.create_index(
        "ix_capacity_exception_work_centre_id", "capacity_exception", ["work_centre_id"]
    )
    op.create_index(
        "ix_capacity_exception_exception_date", "capacity_exception", ["exception_date"]
    )

    op.create_table(
        "capacity_booking",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_centre_id", sa.Integer(), nullable=False),
        sa.Column("sewing_order_id", sa.Integer(), nullable=True),
        sa.Column("cut_order_id", sa.Integer(), nullable=True),
        sa.Column("sales_order_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("minutes", _QTY, nullable=False, server_default="0"),
        sa.Column("status", booking_status, nullable=False, server_default="planned"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["work_centre_id"], ["work_centre.id"]),
        sa.ForeignKeyConstraint(["sewing_order_id"], ["sewing_order.id"]),
        sa.ForeignKeyConstraint(["cut_order_id"], ["cut_order.id"]),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "work_centre_id", "sewing_order_id", "cut_order_id", "sales_order_id",
        "start_date", "end_date", "status",
    ):
        op.create_index(f"ix_capacity_booking_{column}", "capacity_booking", [column])

    # ---- MRP -------------------------------------------------------------- #
    op.create_table(
        "mrp_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_number", sa.String(), nullable=False),
        sa.Column("horizon_start", sa.Date(), nullable=False),
        sa.Column("horizon_end", sa.Date(), nullable=False),
        sa.Column("bucket_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("status", mrp_run_status, nullable=False, server_default="draft"),
        sa.Column("generated_by", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_number", name="uq_mrp_run_number"),
    )
    op.create_index("ix_mrp_run_run_number", "mrp_run", ["run_number"])
    op.create_index("ix_mrp_run_status", "mrp_run", ["status"])

    op.create_table(
        "mrp_bucket",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_start", sa.Date(), nullable=False),
        sa.Column("bucket_end", sa.Date(), nullable=False),
        sa.Column("opening_balance", _QTY, nullable=False, server_default="0"),
        sa.Column("gross_requirement", _QTY, nullable=False, server_default="0"),
        sa.Column("scheduled_receipts", _QTY, nullable=False, server_default="0"),
        sa.Column("net_requirement", _QTY, nullable=False, server_default="0"),
        sa.Column("planned_order_qty", _QTY, nullable=False, server_default="0"),
        sa.Column("projected_available", _QTY, nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["run_id"], ["mrp_run.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["material.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("run_id", "material_id", "sequence", "bucket_start"):
        op.create_index(f"ix_mrp_bucket_{column}", "mrp_bucket", [column])

    op.create_table(
        "mrp_demand_source",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bucket_id", sa.Integer(), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=False),
        sa.Column("quantity", _QTY, nullable=False, server_default="0"),
        sa.Column("need_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["bucket_id"], ["mrp_bucket.id"]),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mrp_demand_source_bucket_id", "mrp_demand_source", ["bucket_id"])
    op.create_index(
        "ix_mrp_demand_source_sales_order_id", "mrp_demand_source", ["sales_order_id"]
    )

    op.create_table(
        "planned_order",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("quantity", _QTY, nullable=False, server_default="0"),
        sa.Column("need_date", sa.Date(), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status", planned_order_status, nullable=False, server_default="planned"
        ),
        sa.Column("requisition_id", sa.Integer(), nullable=True),
        sa.Column("past_due", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["mrp_run.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["material.id"]),
        sa.ForeignKeyConstraint(["requisition_id"], ["purchase_requisition.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "run_id", "material_id", "need_date", "release_date", "status",
        "requisition_id", "past_due",
    ):
        op.create_index(f"ix_planned_order_{column}", "planned_order", [column])


def downgrade() -> None:
    op.drop_table("planned_order")
    op.drop_table("mrp_demand_source")
    op.drop_table("mrp_bucket")
    op.drop_table("mrp_run")
    op.drop_table("capacity_booking")
    op.drop_table("capacity_exception")
    op.drop_table("work_centre")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in (
            planned_order_status, mrp_run_status, booking_status, work_centre_type,
        ):
            enum.drop(bind, checkfirst=True)
