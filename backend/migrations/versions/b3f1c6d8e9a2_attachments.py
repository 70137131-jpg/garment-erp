"""file attachments linked to business records

Revision ID: b3f1c6d8e9a2
Revises: e2b9d4f6a813
Create Date: 2026-07-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3f1c6d8e9a2"
down_revision: Union[str, Sequence[str], None] = "e2b9d4f6a813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attachment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("stored_name", sa.String(length=100), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_attachment_entity_type", "attachment", ["entity_type"])
    op.create_index("ix_attachment_entity_id", "attachment", ["entity_id"])
    op.create_index("ix_attachment_uploaded_by", "attachment", ["uploaded_by"])
    op.create_index("ix_attachment_stored_name", "attachment", ["stored_name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_attachment_stored_name", table_name="attachment")
    op.drop_index("ix_attachment_uploaded_by", table_name="attachment")
    op.drop_index("ix_attachment_entity_id", table_name="attachment")
    op.drop_index("ix_attachment_entity_type", table_name="attachment")
    op.drop_table("attachment")
