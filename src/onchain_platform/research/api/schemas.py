"""API response envelopes (DOC-015 § Response Shape).

The ONLY bespoke response model in the API is the pagination envelope — every
resource body is a Canonical Schema directly. Single-resource GETs return the
schema body itself; collection endpoints return this envelope.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PaginationInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    next_cursor: str | None = None
    has_more: bool = False


class PaginatedResponse(BaseModel, Generic[T]):  # noqa: UP046 — Pydantic needs Generic[T]
    model_config = ConfigDict(frozen=True)

    items: list[T]
    pagination: PaginationInfo = Field(default_factory=PaginationInfo)
