"""actual costing runs and variance decomposition

Revision ID: b7d3e1f9c084
Revises: a2c3d4e5f6a7
Create Date: 2026-07-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b7d3e1f9c084"
down_revision: Union[str, Sequence[str], None] = "a2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


run_status = sa.Enum("draft", "posted", name="actualcostrunstatus")
variance_type = sa.Enum(
    "material_price",
    "material_usage",
    "labour_rate",
    "labour_efficiency",
    "overhead",
    "subcontract",
    name="variancetype",
)

_MONEY = sa.Numeric(precision=18, scale=2)
_QTY = sa.Numeric(precision=18, scale=4)
_RATE = sa.Numeric(precision=18, scale=6)


def upgrade() -> None:
    bind = op.get_bind()
    run_status.create(bind, checkfirst=True)
    variance_type.create(bind, checkfirst=True)

    op.create_table(
        "actual_cost_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_number", sa.String(), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=False),
        sa.Column("cost_sheet_version_id", sa.Integer(), nullable=True),
        sa.Column("status", run_status, nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("produced_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("std_material_cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("std_labour_cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("std_overhead_cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("std_total_cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("actual_material_cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("actual_labour_cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("actual_overhead_cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("actual_subcontract_cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("actual_total_cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("std_material_qty", _QTY, nullable=False, server_default="0"),
        sa.Column("actual_material_qty", _QTY, nullable=False, server_default="0"),
        sa.Column("std_minutes", _QTY, nullable=False, server_default="0"),
        sa.Column("actual_minutes", _QTY, nullable=False, server_default="0"),
        sa.Column("std_rate_per_min", _RATE, nullable=False, server_default="0"),
        sa.Column("actual_rate_per_min", _RATE, nullable=False, server_default="0"),
        sa.Column("journal_entry_id", sa.Integer(), nullable=True),
        sa.Column("posted_at", sa.String(), nullable=True),
        sa.Column("posted_by", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.ForeignKeyConstraint(["cost_sheet_version_id"], ["cost_sheet_version.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_number", name="uq_actual_cost_run_number"),
    )
    op.create_index("ix_actual_cost_run_run_number", "actual_cost_run", ["run_number"])
    op.create_index(
        "ix_actual_cost_run_sales_order_id", "actual_cost_run", ["sales_order_id"]
    )
    op.create_index(
        "ix_actual_cost_run_cost_sheet_version_id",
        "actual_cost_run",
        ["cost_sheet_version_id"],
    )
    op.create_index("ix_actual_cost_run_status", "actual_cost_run", ["status"])
    op.create_index(
        "ix_actual_cost_run_journal_entry_id", "actual_cost_run", ["journal_entry_id"]
    )

    op.create_table(
        "cost_variance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("variance_type", variance_type, nullable=False),
        sa.Column("standard_amount", _MONEY, nullable=False, server_default="0"),
        sa.Column("actual_amount", _MONEY, nullable=False, server_default="0"),
        sa.Column("amount", _MONEY, nullable=False, server_default="0"),
        sa.Column("explanation", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["actual_cost_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_variance_run_id", "cost_variance", ["run_id"])
    op.create_index(
        "ix_cost_variance_variance_type", "cost_variance", ["variance_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_cost_variance_variance_type", table_name="cost_variance")
    op.drop_index("ix_cost_variance_run_id", table_name="cost_variance")
    op.drop_table("cost_variance")

    op.drop_index("ix_actual_cost_run_journal_entry_id", table_name="actual_cost_run")
    op.drop_index("ix_actual_cost_run_status", table_name="actual_cost_run")
    op.drop_index(
        "ix_actual_cost_run_cost_sheet_version_id", table_name="actual_cost_run"
    )
    op.drop_index("ix_actual_cost_run_sales_order_id", table_name="actual_cost_run")
    op.drop_index("ix_actual_cost_run_run_number", table_name="actual_cost_run")
    op.drop_table("actual_cost_run")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        variance_type.drop(bind, checkfirst=True)
        run_status.drop(bind, checkfirst=True)
