from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, Session, SQLModel, select


class DocumentSequence(SQLModel, table=True):
    """Backing store for gap-free, per-type, per-year document numbers
    (blueprint A3: 'Document numbering is controlled and sequential')."""

    __tablename__ = "document_sequence"

    doc_type: str = Field(primary_key=True)
    year: int = Field(primary_key=True)
    last_number: int = Field(default=0)


def next_document_number(
    session: Session,
    doc_type: str,
    prefix: str,
    *,
    year: Optional[int] = None,
    width: int = 5,
) -> str:
    """Issue the next number for a document type, e.g. ``SO-2026-00042``.

    The row is locked ``FOR UPDATE`` so concurrent callers cannot collide.
    On SQLite the lock clause is a no-op but writes serialise anyway; on
    Postgres it provides the real guarantee.
    """

    year = year or datetime.now(timezone.utc).year

    row = session.get(DocumentSequence, (doc_type, year))
    if row is None:
        row = DocumentSequence(doc_type=doc_type, year=year, last_number=0)
        session.add(row)
        session.flush()

    row = session.exec(
        select(DocumentSequence)
        .where(
            DocumentSequence.doc_type == doc_type,
            DocumentSequence.year == year,
        )
        .with_for_update()
    ).one()

    row.last_number += 1
    session.add(row)
    session.flush()

    return f"{prefix}-{year}-{row.last_number:0{width}d}"
