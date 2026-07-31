"""Idempotency keys for capture endpoints.

Locked-in decision (build-plan): shop-floor / capture endpoints are idempotent
via client-supplied keys, so a retried request (flaky network, offline sync in
V1) never double-posts stock or double-counts output. A handler first claims
the key, then records the id of the resource it created in the same
transaction; a replay short-circuits to that resource instead of running again.
"""

from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, select

from .audit import utcnow


class IdempotencyKey(SQLModel, table=True):
    __tablename__ = "idempotency_key"
    __table_args__ = (
        UniqueConstraint("scope", "client_key", name="uq_idempotency_key_scope_client_key"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    client_key: str = Field(index=True)
    scope: str = Field(index=True)  # endpoint / operation name
    # The key is claimed before the business transaction is executed.  It is
    # filled only once the resource has been created in that same transaction.
    resource_id: Optional[int] = Field(default=None)
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())


class IdempotencyInProgress(Exception):
    """A previous request owns the key but has not completed its resource."""


def claim(session: Session, scope: str, client_key: str) -> IdempotencyKey:
    """Atomically claim a key or return the record created by its winner.

    The unique constraint is the concurrency boundary.  A nested transaction
    contains the expected duplicate-key error so a losing request can still
    read the winning record from its outer business transaction.
    """
    key = IdempotencyKey(scope=scope, client_key=client_key)
    try:
        with session.begin_nested():
            session.add(key)
            session.flush()
    except IntegrityError:
        key = session.exec(
            select(IdempotencyKey).where(
                IdempotencyKey.scope == scope,
                IdempotencyKey.client_key == client_key,
            )
        ).one()
        if key.resource_id is None:
            raise IdempotencyInProgress
    return key


def complete(session: Session, key: IdempotencyKey, resource_id: int) -> None:
    """Attach the created resource to a key claimed by this request."""
    if key.resource_id is not None:
        raise ValueError("Cannot complete an idempotency key that already has a resource")
    key.resource_id = resource_id
    session.add(key)
    session.flush()
