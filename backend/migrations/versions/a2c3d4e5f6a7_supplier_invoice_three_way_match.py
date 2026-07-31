"""supplier invoices and three-way match controls

Revision ID: a2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


match_status = sa.Enum("pending", "matched", "exception", name="threewaymatchstatus")


def upgrade() -> None:
    with op.batch_alter_table("ap_bill") as batch_op:
        batch_op.add_column(
            sa.Column("purchase_order_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("supplier_invoice_number", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("match_status", match_status, nullable=False, server_default="pending")
        )
        batch_op.add_column(
            sa.Column("received_amount", sa.Numeric(precision=14, scale=2), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("variance_amount", sa.Numeric(precision=14, scale=2), nullable=False, server_default="0")
        )
        batch_op.create_index("ix_ap_bill_purchase_order_id", ["purchase_order_id"])
        batch_op.create_index("ix_ap_bill_supplier_invoice_number", ["supplier_invoice_number"])
        batch_op.create_index("ix_ap_bill_match_status", ["match_status"])
        batch_op.create_unique_constraint(
            "uq_ap_bill_supplier_invoice_number",
            ["supplier_id", "supplier_invoice_number"],
        )
        batch_op.create_foreign_key(
            "fk_ap_bill_purchase_order_id_purchase_order",
            "purchase_order",
            ["purchase_order_id"],
            ["id"],
        )
    op.execute("UPDATE ap_bill SET received_amount = amount")


def downgrade() -> None:
    with op.batch_alter_table("ap_bill") as batch_op:
        batch_op.drop_constraint("uq_ap_bill_supplier_invoice_number", type_="unique")
        batch_op.drop_constraint("fk_ap_bill_purchase_order_id_purchase_order", type_="foreignkey")
        batch_op.drop_index("ix_ap_bill_match_status")
        batch_op.drop_index("ix_ap_bill_supplier_invoice_number")
        batch_op.drop_index("ix_ap_bill_purchase_order_id")
        batch_op.drop_column("variance_amount")
        batch_op.drop_column("received_amount")
        batch_op.drop_column("match_status")
        batch_op.drop_column("supplier_invoice_number")
        batch_op.drop_column("purchase_order_id")
    if op.get_bind().dialect.name == "postgresql":
        match_status.drop(op.get_bind(), checkfirst=True)
