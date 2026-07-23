"""business operations, controls, reports and document support

Revision ID: d4e9a1f2b7c3
Revises: c18a7d9e42f1
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "d4e9a1f2b7c3"
down_revision: Union[str, Sequence[str], None] = "c18a7d9e42f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("roll") as batch_op:
        batch_op.add_column(sa.Column("parent_roll_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_roll_parent", "roll", ["parent_roll_id"], ["id"])
        batch_op.create_index("ix_roll_parent_roll_id", ["parent_roll_id"], unique=False)

    with op.batch_alter_table("purchase_order") as batch_op:
        batch_op.add_column(sa.Column("requires_approval", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column("approved_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("ar_invoice") as batch_op:
        batch_op.add_column(sa.Column("invoice_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("due_date", sa.Date(), nullable=True))
        batch_op.create_index("ix_ar_invoice_due_date", ["due_date"], unique=False)

    with op.batch_alter_table("ap_bill") as batch_op:
        batch_op.add_column(sa.Column("bill_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("due_date", sa.Date(), nullable=True))
        batch_op.create_index("ix_ap_bill_due_date", ["due_date"], unique=False)

    with op.batch_alter_table("four_point_defect") as batch_op:
        batch_op.add_column(sa.Column("defect_code", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("category", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index("ix_four_point_defect_defect_code", ["defect_code"], unique=False)
        batch_op.create_index("ix_four_point_defect_category", ["category"], unique=False)

    op.create_table(
        "warehouse_operation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_number", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("operation_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reference", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_warehouse_operation_operation_number", "warehouse_operation", ["operation_number"], unique=True)
    op.create_index("ix_warehouse_operation_operation_type", "warehouse_operation", ["operation_type"], unique=False)
    op.create_table(
        "warehouse_operation_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("roll_id", sa.Integer(), nullable=True),
        sa.Column("result_roll_id", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("from_warehouse", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("from_location", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("to_warehouse", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("to_location", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("old_grade", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("new_grade", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("system_qty", sa.Numeric(18, 4), nullable=True),
        sa.Column("counted_qty", sa.Numeric(18, 4), nullable=True),
        sa.Column("variance_qty", sa.Numeric(18, 4), nullable=True),
        sa.ForeignKeyConstraint(["operation_id"], ["warehouse_operation.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["material.id"]),
        sa.ForeignKeyConstraint(["roll_id"], ["roll.id"]),
        sa.ForeignKeyConstraint(["result_roll_id"], ["roll.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("operation_id", "material_id", "roll_id"):
        op.create_index(f"ix_warehouse_operation_line_{column}", "warehouse_operation_line", [column], unique=False)

    op.create_table(
        "sales_order_revision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sales_order_id", "revision_no", name="uq_sales_order_revision"),
    )
    op.create_index("ix_sales_order_revision_sales_order_id", "sales_order_revision", ["sales_order_id"], unique=False)
    op.create_index("ix_sales_order_revision_action", "sales_order_revision", ["action"], unique=False)

    op.create_table(
        "purchase_order_revision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_order.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("purchase_order_id", "revision_no", name="uq_purchase_order_revision"),
    )
    op.create_index("ix_purchase_order_revision_purchase_order_id", "purchase_order_revision", ["purchase_order_id"], unique=False)
    op.create_index("ix_purchase_order_revision_action", "purchase_order_revision", ["action"], unique=False)

    op.create_table(
        "production_route",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("route_number", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("style_id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_production_route_route_number", "production_route", ["route_number"], unique=True)
    op.create_index("ix_production_route_style_id", "production_route", ["style_id"], unique=False)
    op.create_table(
        "production_route_step",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("operation", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("work_center", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("standard_minutes", sa.Numeric(18, 6), nullable=False),
        sa.Column("subcontract_allowed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["route_id"], ["production_route.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("route_id", "sequence", name="uq_route_step_sequence"),
    )
    op.create_index("ix_production_route_step_route_id", "production_route_step", ["route_id"], unique=False)
    op.create_table(
        "wip_movement",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("movement_number", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sewing_order_id", sa.Integer(), nullable=False),
        sa.Column("route_step_id", sa.Integer(), nullable=False),
        sa.Column("quantity_in", sa.Integer(), nullable=False),
        sa.Column("quantity_out", sa.Integer(), nullable=False),
        sa.Column("rejected_qty", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("recorded_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["sewing_order_id"], ["sewing_order.id"]),
        sa.ForeignKeyConstraint(["route_step_id"], ["production_route_step.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wip_movement_movement_number", "wip_movement", ["movement_number"], unique=True)
    op.create_index("ix_wip_movement_sewing_order_id", "wip_movement", ["sewing_order_id"], unique=False)
    op.create_index("ix_wip_movement_route_step_id", "wip_movement", ["route_step_id"], unique=False)

    op.create_table(
        "lab_test",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("test_number", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("roll_id", sa.Integer(), nullable=True),
        sa.Column("supplier_id", sa.Integer(), nullable=True),
        sa.Column("test_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("method", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("specification", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("measured_value", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("submitted_date", sa.Date(), nullable=True),
        sa.Column("completed_date", sa.Date(), nullable=True),
        sa.Column("laboratory", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("certificate_reference", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("tested_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["material_id"], ["material.id"]),
        sa.ForeignKeyConstraint(["roll_id"], ["roll.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lab_test_test_number", "lab_test", ["test_number"], unique=True)
    for column in ("material_id", "roll_id", "supplier_id", "test_type", "status"):
        op.create_index(f"ix_lab_test_{column}", "lab_test", [column], unique=False)


def downgrade() -> None:
    op.drop_table("lab_test")
    op.drop_table("wip_movement")
    op.drop_table("production_route_step")
    op.drop_table("production_route")
    op.drop_table("purchase_order_revision")
    op.drop_table("sales_order_revision")
    op.drop_table("warehouse_operation_line")
    op.drop_table("warehouse_operation")
    with op.batch_alter_table("four_point_defect") as batch_op:
        batch_op.drop_index("ix_four_point_defect_category")
        batch_op.drop_index("ix_four_point_defect_defect_code")
        batch_op.drop_column("category")
        batch_op.drop_column("defect_code")
    with op.batch_alter_table("ap_bill") as batch_op:
        batch_op.drop_index("ix_ap_bill_due_date")
        batch_op.drop_column("due_date")
        batch_op.drop_column("bill_date")
    with op.batch_alter_table("ar_invoice") as batch_op:
        batch_op.drop_index("ix_ar_invoice_due_date")
        batch_op.drop_column("due_date")
        batch_op.drop_column("invoice_date")
    with op.batch_alter_table("purchase_order") as batch_op:
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by")
        batch_op.drop_column("requires_approval")
    with op.batch_alter_table("roll") as batch_op:
        batch_op.drop_index("ix_roll_parent_roll_id")
        batch_op.drop_constraint("fk_roll_parent", type_="foreignkey")
        batch_op.drop_column("parent_roll_id")
