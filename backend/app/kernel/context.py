"""Ambient 'current session' for event subscribers.

Finance auto-posting (blueprint 8.3) must land in the *same* transaction as the
operation that emitted the event, so a rolled-back goods receipt never leaves an
orphan journal. The synchronous event dispatcher passes only the event, so the
active :class:`~sqlmodel.Session` is exposed here via a context variable set for
the life of each request (see :func:`app.db.get_session`).
"""

import contextvars
from typing import Optional

from sqlmodel import Session

_current_session: contextvars.ContextVar[Optional[Session]] = contextvars.ContextVar(
    "current_session", default=None
)


def set_current_session(session: Optional[Session]):
    return _current_session.set(session)


def get_current_session() -> Optional[Session]:
    return _current_session.get()


def reset_current_session(token) -> None:
    _current_session.reset(token)
