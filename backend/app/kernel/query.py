"""Shared list-query and export helpers for operational registers."""

import csv
import io
from typing import Iterable, Mapping, Sequence

from fastapi import HTTPException
from fastapi.responses import StreamingResponse


MAX_PAGE_SIZE = 500


def page_bounds(offset: int = 0, limit: int = 100) -> tuple[int, int]:
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset cannot be negative")
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be between 1 and {MAX_PAGE_SIZE}",
        )
    return offset, limit


def csv_download(
    filename: str,
    columns: Sequence[tuple[str, str]],
    rows: Iterable[Mapping],
) -> StreamingResponse:
    """Stream a UTF-8 CSV with stable column ordering and spreadsheet-safe cells."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([label for _, label in columns])
    for row in rows:
        values = []
        for key, _ in columns:
            value = row.get(key, "")
            rendered = "" if value is None else str(value)
            # Prevent formula injection when an export is opened in a spreadsheet.
            if rendered.startswith(("=", "+", "-", "@")):
                rendered = "'" + rendered
            values.append(rendered)
        writer.writerow(values)
    payload = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
