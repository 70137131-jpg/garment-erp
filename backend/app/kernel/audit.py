from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin(SQLModel):
    """Audit stamps applied to every persisted entity (blueprint 9.4).

    `created_by` / `updated_by` are populated from the acting user once real
    auth lands; for now they are optional so the skeleton runs without it.
    """

    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
    created_by: Optional[str] = Field(default=None)
    updated_by: Optional[str] = Field(default=None)
