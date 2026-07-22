"""production RBAC and server-side authentication

Revision ID: 9f6b2aa71c4e
Revises: 6b5281d83d4d
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "9f6b2aa71c4e"
down_revision: Union[str, Sequence[str], None] = "6b5281d83d4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(length=320), nullable=False),
        sa.Column("display_name", sqlmodel.sql.sqltypes.AutoString(length=120), nullable=False),
        sa.Column("password_hash", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_user_email"), "app_user", ["email"], unique=True)
    op.create_index(op.f("ix_app_user_is_active"), "app_user", ["is_active"], unique=False)

    op.create_table(
        "auth_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("ip_address", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("user_agent", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_session_expires_at"), "auth_session", ["expires_at"], unique=False)
    op.create_index(op.f("ix_auth_session_revoked_at"), "auth_session", ["revoked_at"], unique=False)
    op.create_index(op.f("ix_auth_session_token_hash"), "auth_session", ["token_hash"], unique=True)
    op.create_index(op.f("ix_auth_session_user_id"), "auth_session", ["user_id"], unique=False)

    op.create_table(
        "security_audit_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(length=320), nullable=True),
        sa.Column("ip_address", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("detail", sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_security_audit_event_actor_user_id"), "security_audit_event", ["actor_user_id"], unique=False)
    op.create_index(op.f("ix_security_audit_event_event_type"), "security_audit_event", ["event_type"], unique=False)
    op.create_index(op.f("ix_security_audit_event_occurred_at"), "security_audit_event", ["occurred_at"], unique=False)
    op.create_index(op.f("ix_security_audit_event_target_user_id"), "security_audit_event", ["target_user_id"], unique=False)

    op.create_table(
        "user_role",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["granted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "role IN ('admin','merchandiser','procurement','stores',"
            "'cutting_supervisor','sewing_supervisor','quality_inspector',"
            "'finance','planner','readonly')",
            name="ck_user_role_valid_role",
        ),
        sa.PrimaryKeyConstraint("user_id", "role"),
    )


def downgrade() -> None:
    op.drop_table("user_role")
    op.drop_index(op.f("ix_security_audit_event_target_user_id"), table_name="security_audit_event")
    op.drop_index(op.f("ix_security_audit_event_occurred_at"), table_name="security_audit_event")
    op.drop_index(op.f("ix_security_audit_event_event_type"), table_name="security_audit_event")
    op.drop_index(op.f("ix_security_audit_event_actor_user_id"), table_name="security_audit_event")
    op.drop_table("security_audit_event")
    op.drop_index(op.f("ix_auth_session_user_id"), table_name="auth_session")
    op.drop_index(op.f("ix_auth_session_token_hash"), table_name="auth_session")
    op.drop_index(op.f("ix_auth_session_revoked_at"), table_name="auth_session")
    op.drop_index(op.f("ix_auth_session_expires_at"), table_name="auth_session")
    op.drop_table("auth_session")
    op.drop_index(op.f("ix_app_user_is_active"), table_name="app_user")
    op.drop_index(op.f("ix_app_user_email"), table_name="app_user")
    op.drop_table("app_user")
