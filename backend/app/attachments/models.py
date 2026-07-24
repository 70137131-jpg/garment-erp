from datetime import datetime
from typing import Optional

from pydantic import ConfigDict
from sqlmodel import Field, SQLModel

from ..kernel.audit import utcnow


class Attachment(SQLModel, table=True):
    """A file attached to a business document (PO PDF, lab-dip photo, …).

    The bytes live on disk under a server-generated name; only metadata is in
    the database. ``stored_name`` is a UUID-based name so client-supplied
    filenames can never influence filesystem paths.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    entity_type: str = Field(index=True, max_length=40)
    entity_id: int = Field(index=True)
    filename: str = Field(max_length=255)
    content_type: str = Field(max_length=100)
    size_bytes: int
    sha256: str = Field(max_length=64)
    stored_name: str = Field(unique=True, max_length=100)
    uploaded_by: Optional[int] = Field(default=None, foreign_key="app_user.id", index=True)
    uploaded_at: datetime = Field(default_factory=utcnow, nullable=False)


class AttachmentRead(SQLModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    entity_id: int
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by: Optional[int]
    uploaded_at: datetime
