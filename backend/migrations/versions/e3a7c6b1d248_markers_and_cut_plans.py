"""markers with derived efficiency, and solved cut/lay plans

Revision ID: e3a7c6b1d248
Revises: d1f5b8c3e097
Create Date: 2026-07-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e3a7c6b1d248"
down_revision: Union[str, Sequence[str], None] = "d1f5b8c3e097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


marker_status = sa.Enum("draft", "approved", "retired", name="markerstatus")
cut_plan_status = sa.Enum("draft", "approved", "cancelled", name="cutplanstatus")

_QTY = sa.Numeric(precision=18, scale=4)
_RATE = sa.Numeric(precision=18, scale=6)


def upgrade() -> None:
    bind = op.get_bind()
    marker_status.create(bind, checkfirst=True)
    cut_plan_status.create(bind, checkfirst=True)

    op.create_table(
        "marker",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("marker_code", sa.String(length=40), nullable=False),
        sa.Column("style_id", sa.Integer(), nullable=False),
        sa.Column("bom_version_id", sa.Integer(), nullable=True),
        sa.Column("width_cm", _QTY, nullable=False, server_default="0"),
        sa.Column("length_cm", _QTY, nullable=False, server_default="0"),
        sa.Column("pattern_area_cm2", _QTY, nullable=False, server_default="0"),
        sa.Column("efficiency_pct", _RATE, nullable=False, server_default="0"),
        sa.Column("max_plies", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", marker_status, nullable=False, server_default="draft"),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"]),
        sa.ForeignKeyConstraint(["bom_version_id"], ["bom_version.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("marker_code", name="uq_marker_code"),
    )
    for column in ("marker_code", "style_id", "bom_version_id", "status"):
        op.create_index(f"ix_marker_{column}", "marker", [column])

    op.create_table(
        "marker_size",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("marker_id", sa.Integer(), nullable=False),
        sa.Column("size_label", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["marker_id"], ["marker.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("marker_id", "size_label", name="uq_marker_size_label"),
    )
    op.create_index("ix_marker_size_marker_id", "marker_size", ["marker_id"])
    op.create_index("ix_marker_size_size_label", "marker_size", ["size_label"])

    op.create_table(
        "cut_plan",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_number", sa.String(), nullable=False),
        sa.Column("style_id", sa.Integer(), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=True),
        sa.Column("cut_order_id", sa.Integer(), nullable=True),
        sa.Column("fabric_material_id", sa.Integer(), nullable=True),
        sa.Column("width_cm", _QTY, nullable=False, server_default="0"),
        sa.Column("max_plies", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", cut_plan_status, nullable=False, server_default="draft"),
        sa.Column("total_fabric_cm", _QTY, nullable=False, server_default="0"),
        sa.Column("total_plies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lay_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required_pieces", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("planned_pieces", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overcut_pieces", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weighted_efficiency_pct", _RATE, nullable=False, server_default="0"),
        sa.Column("bom_fabric_cm", _QTY, nullable=True),
        sa.Column("algorithm", sa.String(length=60), nullable=False,
                  server_default="greedy-coverage+ply-reduction"),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"]),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.ForeignKeyConstraint(["cut_order_id"], ["cut_order.id"]),
        sa.ForeignKeyConstraint(["fabric_material_id"], ["material.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_number", name="uq_cut_plan_number"),
    )
    for column in (
        "plan_number", "style_id", "sales_order_id", "cut_order_id",
        "fabric_material_id", "status",
    ):
        op.create_index(f"ix_cut_plan_{column}", "cut_plan", [column])

    op.create_table(
        "cut_plan_lay",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("marker_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fabric_cm", _QTY, nullable=False, server_default="0"),
        sa.Column("pieces", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["plan_id"], ["cut_plan.id"]),
        sa.ForeignKeyConstraint(["marker_id"], ["marker.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cut_plan_lay_plan_id", "cut_plan_lay", ["plan_id"])
    op.create_index("ix_cut_plan_lay_marker_id", "cut_plan_lay", ["marker_id"])

    op.create_table(
        "cut_plan_demand",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("size_label", sa.String(length=20), nullable=False),
        sa.Column("required_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("planned_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overcut_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["plan_id"], ["cut_plan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cut_plan_demand_plan_id", "cut_plan_demand", ["plan_id"])


def downgrade() -> None:
    op.drop_table("cut_plan_demand")
    op.drop_table("cut_plan_lay")
    op.drop_table("cut_plan")
    op.drop_table("marker_size")
    op.drop_table("marker")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        cut_plan_status.drop(bind, checkfirst=True)
        marker_status.drop(bind, checkfirst=True)
