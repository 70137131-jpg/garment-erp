"""make idempotency key claims unique and completable

Revision ID: f1a2b3c4d5e6
Revises: d8e4f2a9c6b1
Create Date: 2026-07-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "d8e4f2a9c6b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Preserve the earliest successful request for each historical duplicate.
    # The derived table is needed by MySQL and remains valid for SQLite/Postgres.
    op.execute(
        """
        DELETE FROM idempotency_key
        WHERE id NOT IN (
            SELECT winner_id FROM (
                SELECT MIN(id) AS winner_id
                FROM idempotency_key
                GROUP BY scope, client_key
            ) AS duplicate_winners
        )
        """
    )
    with op.batch_alter_table("idempotency_key") as batch_op:
        batch_op.alter_column("resource_id", existing_type=sa.Integer(), nullable=True)
        batch_op.create_unique_constraint(
            "uq_idempotency_key_scope_client_key", ["scope", "client_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("idempotency_key") as batch_op:
        batch_op.drop_constraint("uq_idempotency_key_scope_client_key", type_="unique")
        batch_op.alter_column("resource_id", existing_type=sa.Integer(), nullable=False)
