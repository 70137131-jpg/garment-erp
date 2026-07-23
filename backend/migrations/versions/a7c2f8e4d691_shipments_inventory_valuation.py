"""partial shipments and inventory valuation

Revision ID: a7c2f8e4d691
Revises: f5a8c3d9e712
Create Date: 2026-07-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "a7c2f8e4d691"
down_revision: Union[str, Sequence[str], None] = "f5a8c3d9e712"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("material") as batch_op:
        batch_op.add_column(sa.Column("valuation_method", sqlmodel.sql.sqltypes.AutoString(), server_default="weighted_average", nullable=False))

    with op.batch_alter_table("stock_ledger_entry") as batch_op:
        batch_op.add_column(sa.Column("unit_cost", sa.Numeric(18, 2), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("extended_cost", sa.Numeric(18, 2), server_default="0", nullable=False))

    op.create_table(
        "inventory_cost_layer",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("receipt_ledger_entry_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("roll_id", sa.Integer(), nullable=True),
        sa.Column("warehouse", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("location", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("received_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("remaining_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["receipt_ledger_entry_id"], ["stock_ledger_entry.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["material.id"]),
        sa.ForeignKeyConstraint(["roll_id"], ["roll.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_cost_layer_receipt_ledger_entry_id", "inventory_cost_layer", ["receipt_ledger_entry_id"], unique=False)
    op.create_index("ix_inventory_cost_layer_material_id", "inventory_cost_layer", ["material_id"], unique=False)
    op.create_index("ix_inventory_cost_layer_roll_id", "inventory_cost_layer", ["roll_id"], unique=False)

    op.create_table(
        "shipment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_number", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=False),
        sa.Column("shipment_date", sa.Date(), nullable=True),
        sa.Column("shipping_reference", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("destination", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cost_of_goods", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shipment_number"),
    )
    op.create_index("ix_shipment_shipment_number", "shipment", ["shipment_number"], unique=True)
    op.create_index("ix_shipment_sales_order_id", "shipment", ["sales_order_id"], unique=False)
    op.create_index("ix_shipment_shipping_reference", "shipment", ["shipping_reference"], unique=False)
    op.create_index("ix_shipment_status", "shipment", ["status"], unique=False)

    op.create_table(
        "shipment_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("sales_order_line_id", sa.Integer(), nullable=False),
        sa.Column("sales_order_size_cell_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("carton_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipment.id"]),
        sa.ForeignKeyConstraint(["sales_order_line_id"], ["sales_order_line.id"]),
        sa.ForeignKeyConstraint(["sales_order_size_cell_id"], ["sales_order_size_cell.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shipment_line_shipment_id", "shipment_line", ["shipment_id"], unique=False)
    op.create_index("ix_shipment_line_sales_order_line_id", "shipment_line", ["sales_order_line_id"], unique=False)
    op.create_index("ix_shipment_line_sales_order_size_cell_id", "shipment_line", ["sales_order_size_cell_id"], unique=False)

    with op.batch_alter_table("ar_invoice") as batch_op:
        batch_op.add_column(sa.Column("shipment_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_ar_invoice_shipment", "shipment", ["shipment_id"], ["id"])
        batch_op.create_index("ix_ar_invoice_shipment_id", ["shipment_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("ar_invoice") as batch_op:
        batch_op.drop_index("ix_ar_invoice_shipment_id")
        batch_op.drop_constraint("fk_ar_invoice_shipment", type_="foreignkey")
        batch_op.drop_column("shipment_id")
    op.drop_index("ix_shipment_line_sales_order_size_cell_id", table_name="shipment_line")
    op.drop_index("ix_shipment_line_sales_order_line_id", table_name="shipment_line")
    op.drop_index("ix_shipment_line_shipment_id", table_name="shipment_line")
    op.drop_table("shipment_line")
    op.drop_index("ix_shipment_status", table_name="shipment")
    op.drop_index("ix_shipment_shipping_reference", table_name="shipment")
    op.drop_index("ix_shipment_sales_order_id", table_name="shipment")
    op.drop_index("ix_shipment_shipment_number", table_name="shipment")
    op.drop_table("shipment")
    op.drop_index("ix_inventory_cost_layer_roll_id", table_name="inventory_cost_layer")
    op.drop_index("ix_inventory_cost_layer_material_id", table_name="inventory_cost_layer")
    op.drop_index("ix_inventory_cost_layer_receipt_ledger_entry_id", table_name="inventory_cost_layer")
    op.drop_table("inventory_cost_layer")
    with op.batch_alter_table("stock_ledger_entry") as batch_op:
        batch_op.drop_column("extended_cost")
        batch_op.drop_column("unit_cost")
    with op.batch_alter_table("material") as batch_op:
        batch_op.drop_column("valuation_method")
