"""authentication hardening

Revision ID: c18a7d9e42f1
Revises: 9f6b2aa71c4e
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "c18a7d9e42f1"
down_revision: Union[str, Sequence[str], None] = "9f6b2aa71c4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_user") as batch_op:
        batch_op.add_column(
            sa.Column("must_change_password", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.create_index("ix_app_user_must_change_password", ["must_change_password"], unique=False)

    with op.batch_alter_table("auth_session") as batch_op:
        batch_op.add_column(sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.execute(sa.text("UPDATE auth_session SET last_seen_at = created_at WHERE last_seen_at IS NULL"))
    with op.batch_alter_table("auth_session") as batch_op:
        batch_op.alter_column("last_seen_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.create_index("ix_auth_session_last_seen_at", ["last_seen_at"], unique=False)

    op.create_table(
        "login_throttle",
        sa.Column("key_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key_hash"),
    )


def downgrade() -> None:
    op.drop_table("login_throttle")
    with op.batch_alter_table("auth_session") as batch_op:
        batch_op.drop_index("ix_auth_session_last_seen_at")
        batch_op.drop_column("last_seen_at")
    with op.batch_alter_table("app_user") as batch_op:
        batch_op.drop_index("ix_app_user_must_change_password")
        batch_op.drop_column("must_change_password")
