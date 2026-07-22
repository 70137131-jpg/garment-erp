import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.kernel.context import reset_current_session, set_current_session
from app.kernel.rbac import Principal, Role, get_current_principal
from app.main import app


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
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=1,
        email="admin@test.local",
        display_name="Test Administrator",
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
