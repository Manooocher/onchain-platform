"""Shared helpers for resource routers (DOC-015 pagination + errors)."""

from datetime import UTC, datetime
from typing import Any, TypeVar

from fastapi import HTTPException

from onchain_platform.research.api.pagination import (
    InvalidCursor,
    decode_cursor,
    encode_cursor,
)
from onchain_platform.research.api.schemas import PaginatedResponse, PaginationInfo

T = TypeVar("T")


def decode_cursor_or_422(cursor: str | None) -> dict[str, Any] | None:
    """Decode a cursor query param, mapping malformed cursors to 422 (DOC-015
    § Error Handling — a bad cursor is a client bug, never a 500)."""
    if cursor is None:
        return None
    try:
        return decode_cursor(cursor)
    except InvalidCursor as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def build_page(  # noqa: UP047 — standard-library Generic form retained for clarity
    items: list[T], next_cursor_keys: dict[str, Any] | None
) -> PaginatedResponse[T]:
    """Assemble a PaginatedResponse envelope from repo output (DOC-015 §
    Response Shape). `has_more` is true exactly when a next cursor exists."""
    has_more = next_cursor_keys is not None
    return PaginatedResponse(
        items=items,
        pagination=PaginationInfo(
            next_cursor=encode_cursor(next_cursor_keys) if next_cursor_keys else None,
            has_more=has_more,
        ),
    )


def parse_range(start: str | None, end: str | None) -> tuple[datetime | None, datetime | None]:
    """Parse inclusive ISO-8601 start/end query params (422 on malformed)."""
    start_dt = None
    if start is not None:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="malformed start") from exc
    end_dt = None
    if end is not None:
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="malformed end") from exc
    return start_dt, end_dt
