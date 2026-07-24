"""time & action calendars and workforce (employees, attendance, piece rates)

Revision ID: d8e4f2a9c6b1
Revises: b3f1c6d8e9a2
Create Date: 2026-07-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8e4f2a9c6b1"
down_revision: Union[str, Sequence[str], None] = "b3f1c6d8e9a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tna_template",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tna_template_name", "tna_template", ["name"], unique=True)

    op.create_table(
        "tna_template_step",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("tna_template.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("offset_days", sa.Integer(), nullable=False),
        sa.Column("owner_role", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_tna_template_step_template_id", "tna_template_step", ["template_id"])

    op.create_table(
        "tna_milestone",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_order.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("actual_date", sa.Date(), nullable=True),
        sa.Column("owner_role", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tna_milestone_sales_order_id", "tna_milestone", ["sales_order_id"])
    op.create_index("ix_tna_milestone_planned_date", "tna_milestone", ["planned_date"])

    department = sa.Enum(
        "cutting", "sewing", "finishing", "quality", "stores", "maintenance", "admin",
        name="department",
    )
    attendance_status = sa.Enum(
        "present", "absent", "leave", "half_day", "holiday",
        name="attendancestatus",
    )
    bind = op.get_bind()
    department.create(bind, checkfirst=True)
    attendance_status.create(bind, checkfirst=True)

    op.create_table(
        "employee",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_number", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("designation", sa.String(length=80), nullable=False),
        sa.Column("department", department, nullable=False),
        sa.Column("line", sa.String(length=40), nullable=True),
        sa.Column("joined_date", sa.Date(), nullable=True),
        sa.Column("daily_wage", sa.Numeric(14, 2), nullable=False),
        sa.Column("piece_rate_worker", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_employee_employee_number", "employee", ["employee_number"], unique=True)
    op.create_index("ix_employee_department", "employee", ["department"])
    op.create_index("ix_employee_line", "employee", ["line"])
    op.create_index("ix_employee_active", "employee", ["active"])

    op.create_table(
        "attendance_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("status", attendance_status, nullable=False),
        sa.Column("in_time", sa.String(length=5), nullable=True),
        sa.Column("out_time", sa.String(length=5), nullable=True),
        sa.Column("overtime_minutes", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(length=300), nullable=True),
        sa.Column("marked_by", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("employee_id", "attendance_date", name="uq_attendance_employee_day"),
    )
    op.create_index("ix_attendance_record_employee_id", "attendance_record", ["employee_id"])
    op.create_index("ix_attendance_record_attendance_date", "attendance_record", ["attendance_date"])

    op.create_table(
        "piece_rate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("style_id", sa.Integer(), sa.ForeignKey("style.id"), nullable=True),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("rate", sa.Numeric(14, 2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("style_id", "operation", name="uq_piece_rate_style_operation"),
    )
    op.create_index("ix_piece_rate_style_id", "piece_rate", ["style_id"])
    op.create_index("ix_piece_rate_operation", "piece_rate", ["operation"])

    op.create_table(
        "piece_work_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("style_id", sa.Integer(), sa.ForeignKey("style.id"), nullable=True),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("pieces", sa.Integer(), nullable=False),
        sa.Column("rate", sa.Numeric(14, 2), nullable=False),
        sa.Column("entered_by", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_piece_work_record_employee_id", "piece_work_record", ["employee_id"])
    op.create_index("ix_piece_work_record_work_date", "piece_work_record", ["work_date"])
    op.create_index("ix_piece_work_record_style_id", "piece_work_record", ["style_id"])


def downgrade() -> None:
    op.drop_table("piece_work_record")
    op.drop_table("piece_rate")
    op.drop_table("attendance_record")
    op.drop_table("employee")
    op.drop_table("tna_milestone")
    op.drop_table("tna_template_step")
    op.drop_table("tna_template")
    sa.Enum(name="attendancestatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="department").drop(op.get_bind(), checkfirst=True)
