import os
from threading import Barrier, Lock, Thread
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, Session

from app.db import build_engine
from app.kernel.idempotency import (
    IdempotencyInProgress,
    IdempotencyKey,
    claim,
    complete,
)


def test_claim_replays_the_completed_resource(session):
    first = claim(session, "goods_receipt", "scanner-001")
    complete(session, first, 42)
    session.commit()

    replay = claim(session, "goods_receipt", "scanner-001")

    assert replay.id == first.id
    assert replay.resource_id == 42


def test_claim_rejects_an_unfinished_request(session):
    session.add(IdempotencyKey(scope="daily_output", client_key="shift-001"))
    session.commit()

    with pytest.raises(IdempotencyInProgress):
        claim(session, "daily_output", "shift-001")


def test_database_allows_only_one_scope_and_client_key(session):
    session.add(IdempotencyKey(scope="goods_receipt", client_key="scanner-001"))
    session.commit()
    session.add(IdempotencyKey(scope="goods_receipt", client_key="scanner-001"))

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # The same key remains valid for a distinct operation scope.
    session.add(IdempotencyKey(scope="daily_output", client_key="scanner-001"))
    session.commit()


@pytest.mark.concurrency
def test_concurrent_claim_has_one_winner_on_postgres():
    database_url = os.environ.get("TEST_DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        pytest.skip("requires the PostgreSQL CI database")

    engine = build_engine(database_url)
    SQLModel.metadata.create_all(engine)
    barrier = Barrier(2)
    result_lock = Lock()
    scope = f"concurrent_test_{uuid4().hex}"
    winners: list[bool] = []
    resources: list[int | None] = []
    failures: list[BaseException] = []

    def capture() -> None:
        try:
            with Session(engine) as concurrent_session:
                barrier.wait()
                key = claim(concurrent_session, scope, "same-client-key")
                won_claim = key.resource_id is None
                if won_claim:
                    complete(concurrent_session, key, 777)
                concurrent_session.commit()
                with result_lock:
                    winners.append(won_claim)
                    resources.append(key.resource_id)
        except BaseException as exc:  # assert after joining both workers
            with result_lock:
                failures.append(exc)

    workers = [Thread(target=capture) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    engine.dispose()

    assert not failures
    assert winners.count(True) == 1
    assert len(resources) == 2
    assert set(resources) == {777}
