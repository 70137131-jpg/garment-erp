"""Idempotency keys for capture endpoints.

Locked-in decision (build-plan): shop-floor / capture endpoints are idempotent
via client-supplied keys, so a retried request (flaky network, offline sync in
V1) never double-posts stock or double-counts output. A handler records the key
with the id of the resource it created; a replay of the same key short-circuits
to that resource instead of running again.
"""

from typing import Optional

from sqlmodel import Field, Session, SQLModel, select

from .audit import utcnow


class IdempotencyKey(SQLModel, table=True):
    __tablename__ = "idempotency_key"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_key: str = Field(index=True)
    scope: str = Field(index=True)  # endpoint / operation name
    resource_id: int
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())


def find_existing(session: Session, scope: str, client_key: str) -> Optional[int]:
    row = session.exec(
        select(IdempotencyKey).where(
            IdempotencyKey.scope == scope,
            IdempotencyKey.client_key == client_key,
        )
    ).first()
    return row.resource_id if row else None


def record(session: Session, scope: str, client_key: str, resource_id: int) -> None:
    session.add(
        IdempotencyKey(scope=scope, client_key=client_key, resource_id=resource_id)
    )
    session.flush()
