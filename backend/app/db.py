from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, create_engine

from .config import settings
from .kernel.context import set_current_session


def build_engine(database_url: str) -> Engine:
    """Create the app engine with dialect-specific safety settings.

    PostgreSQL connections are pinned to UTC: the app writes aware-UTC
    datetimes into ``timestamp without time zone`` columns, and Postgres
    converts those using the *session* time zone. Without this pin, a database
    server in any non-UTC zone silently skews every session expiry, lockout,
    and audit timestamp.
    """
    if database_url.startswith("sqlite"):
        return create_engine(
            database_url, echo=False, connect_args={"check_same_thread": False}
        )
    if database_url.startswith("postgresql"):
        return create_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            # Sized explicitly rather than left to SQLAlchemy's defaults: the
            # pool is per *process*, so the real ceiling is this multiplied by
            # the number of gunicorn workers. See Settings.db_pool_size.
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=settings.db_pool_recycle_seconds,
            connect_args={"options": "-c timezone=utc"},
        )
    return create_engine(database_url, echo=False)


engine = build_engine(settings.database_url)


def init_db() -> None:
    """Create tables for local/dev convenience.

    Production uses Alembic migrations (``alembic upgrade head``); this is the
    zero-setup path for SQLite dev. Importing ``app.models`` first ensures every
    table is registered on the metadata before create_all runs.
    """
    from . import models  # noqa: F401  (registers all tables)
    from .kernel.immutability import install_immutable_record_guards

    SQLModel.metadata.create_all(engine)
    install_immutable_record_guards(engine)


async def get_session():
    # Async dependency on purpose: it sets the current-session context var in the
    # request's coroutine context, which sync endpoints (run in a threadpool)
    # copy — so event subscribers (finance posting) see the same session and join
    # this transaction. A sync dependency would set the var in a separate
    # threadpool context that never reaches the endpoint.
    with Session(engine) as session:
        set_current_session(session)
        try:
            yield session
        finally:
            set_current_session(None)
