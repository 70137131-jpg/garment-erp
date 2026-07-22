from sqlmodel import SQLModel, Session, create_engine

from .config import settings
from .kernel.context import set_current_session

connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


def init_db() -> None:
    """Create tables for local/dev convenience.

    Production uses Alembic migrations (``alembic upgrade head``); this is the
    zero-setup path for SQLite dev. Importing ``app.models`` first ensures every
    table is registered on the metadata before create_all runs.
    """
    from . import models  # noqa: F401  (registers all tables)

    SQLModel.metadata.create_all(engine)


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
