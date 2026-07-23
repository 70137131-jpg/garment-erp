"""material planning snapshots and purchase requisitions

Revision ID: f5a8c3d9e712
Revises: d4e9a1f2b7c3
Create Date: 2026-07-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "f5a8c3d9e712"
down_revision: Union[str, Sequence[str], None] = "d4e9a1f2b7c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_order_material_requirement",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("required_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("available_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("shortage_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["material.id"]),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sales_order_material_requirement_sales_order_id",
        "sales_order_material_requirement",
        ["sales_order_id"],
        unique=False,
    )
    op.create_index(
        "ix_sales_order_material_requirement_material_id",
        "sales_order_material_requirement",
        ["material_id"],
        unique=False,
    )

    op.create_table(
        "purchase_requisition",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requisition_number", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=True),
        sa.Column("suggested_supplier_id", sa.Integer(), nullable=True),
        sa.Column("requested_date", sa.Date(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.ForeignKeyConstraint(["suggested_supplier_id"], ["supplier.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("requisition_number"),
    )
    op.create_index("ix_purchase_requisition_requisition_number", "purchase_requisition", ["requisition_number"], unique=True)
    op.create_index("ix_purchase_requisition_sales_order_id", "purchase_requisition", ["sales_order_id"], unique=False)
    op.create_index("ix_purchase_requisition_suggested_supplier_id", "purchase_requisition", ["suggested_supplier_id"], unique=False)
    op.create_index("ix_purchase_requisition_status", "purchase_requisition", ["status"], unique=False)

    op.create_table(
        "purchase_requisition_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purchase_requisition_id", sa.Integer(), nullable=False),
        sa.Column("material_requirement_id", sa.Integer(), nullable=True),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("requested_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("ordered_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("uom", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["purchase_requisition_id"], ["purchase_requisition.id"]),
        sa.ForeignKeyConstraint(["material_requirement_id"], ["sales_order_material_requirement.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["material.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_purchase_requisition_line_purchase_requisition_id", "purchase_requisition_line", ["purchase_requisition_id"], unique=False)
    op.create_index("ix_purchase_requisition_line_material_requirement_id", "purchase_requisition_line", ["material_requirement_id"], unique=False)
    op.create_index("ix_purchase_requisition_line_material_id", "purchase_requisition_line", ["material_id"], unique=False)

    op.create_table(
        "purchase_order_requisition_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purchase_order_line_id", sa.Integer(), nullable=False),
        sa.Column("purchase_requisition_line_id", sa.Integer(), nullable=False),
        sa.Column("allocated_qty", sa.Numeric(18, 4), nullable=False),
        sa.ForeignKeyConstraint(["purchase_order_line_id"], ["purchase_order_line.id"]),
        sa.ForeignKeyConstraint(["purchase_requisition_line_id"], ["purchase_requisition_line.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_purchase_order_requisition_line_purchase_order_line_id", "purchase_order_requisition_line", ["purchase_order_line_id"], unique=False)
    op.create_index("ix_purchase_order_requisition_line_purchase_requisition_line_id", "purchase_order_requisition_line", ["purchase_requisition_line_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_purchase_order_requisition_line_purchase_requisition_line_id", table_name="purchase_order_requisition_line")
    op.drop_index("ix_purchase_order_requisition_line_purchase_order_line_id", table_name="purchase_order_requisition_line")
    op.drop_table("purchase_order_requisition_line")
    op.drop_index("ix_purchase_requisition_line_material_id", table_name="purchase_requisition_line")
    op.drop_index("ix_purchase_requisition_line_material_requirement_id", table_name="purchase_requisition_line")
    op.drop_index("ix_purchase_requisition_line_purchase_requisition_id", table_name="purchase_requisition_line")
    op.drop_table("purchase_requisition_line")
    op.drop_index("ix_purchase_requisition_status", table_name="purchase_requisition")
    op.drop_index("ix_purchase_requisition_suggested_supplier_id", table_name="purchase_requisition")
    op.drop_index("ix_purchase_requisition_sales_order_id", table_name="purchase_requisition")
    op.drop_index("ix_purchase_requisition_requisition_number", table_name="purchase_requisition")
    op.drop_table("purchase_requisition")
    op.drop_index("ix_sales_order_material_requirement_material_id", table_name="sales_order_material_requirement")
    op.drop_index("ix_sales_order_material_requirement_sales_order_id", table_name="sales_order_material_requirement")
    op.drop_table("sales_order_material_requirement")
