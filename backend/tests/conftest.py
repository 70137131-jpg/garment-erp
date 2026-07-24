import os

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import build_engine, get_session
from app.kernel.context import reset_current_session, set_current_session
from app.kernel.rbac import Principal, Role, get_current_principal
from app.main import app

# Default: fresh in-memory SQLite per test (fast, zero setup). CI also runs
# the whole suite against PostgreSQL — the production dialect — by setting
#   TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


if TEST_DATABASE_URL:
    from app.kernel.immutability import install_immutable_record_guards

    @pytest.fixture(name="_shared_engine", scope="session")
    def shared_engine_fixture():
        engine = build_engine(TEST_DATABASE_URL)
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        # Production schemas carry these guards via migrations; installing
        # them here keeps Postgres test behavior faithful to production.
        install_immutable_record_guards(engine)
        yield engine
        engine.dispose()

    @pytest.fixture(name="session")
    def session_fixture(_shared_engine):
        # Transaction-per-test isolation: the session joins an outer
        # transaction via savepoints, so its commits stay invisible to other
        # tests and everything rolls back at the end.
        connection = _shared_engine.connect()
        transaction = connection.begin()
        session = Session(connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
            connection.close()

else:

    @pytest.fixture(name="session")
    def session_fixture():
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        # Mirror app.db.get_session: expose the session to event subscribers.
        set_current_session(session)
        return session

    app.dependency_overrides[get_session] = get_session_override
    # Domain tests run as an authenticated administrator. Security behavior is
    # covered explicitly in test_rbac_audit.py; keeping auth out of unrelated
    # fixtures makes their business assertions focused and deterministic.
    # The user must genuinely exist: audit events and role grants carry
    # foreign keys to app_user, which PostgreSQL enforces.
    from app.security.models import User, UserRole

    admin_user = User(
        email="admin@test.local",
        display_name="Test Administrator",
        password_hash="!test-fixture-no-login",
        must_change_password=False,
    )
    session.add(admin_user)
    session.flush()
    session.add(UserRole(user_id=admin_user.id, role=Role.admin.value, granted_by=admin_user.id))
    session.commit()
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=admin_user.id,
        email=admin_user.email,
        display_name=admin_user.display_name,
        roles=frozenset({Role.admin}),
        session_id=1,
    )
    token = set_current_session(session)
    test_client = TestClient(app)
    test_client.get("/auth/csrf")
    test_client.headers["X-CSRF-Token"] = test_client.cookies.get("garment_erp_csrf")
    yield test_client
    reset_current_session(token)
    app.dependency_overrides.clear()


@pytest.fixture(name="finance_client")
def finance_client_fixture(client, session):
    """Client with finance auto-posting wired and the default CoA seeded."""
    from app.finance.posting import register_finance_subscribers
    from app.finance.service import seed_chart_of_accounts

    register_finance_subscribers()
    seed_chart_of_accounts(session)
    session.commit()
    yield client
