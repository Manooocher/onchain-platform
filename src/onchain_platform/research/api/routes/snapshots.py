"""Observation Snapshot endpoints (DOC-015 Endpoint Catalog).

`/v1/entities/{id}/snapshots` — ObservationSnapshot history, cursor-paginated.
"""

from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.timescale import repositories as ts_repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.routes._common import (
    build_page,
    decode_cursor_or_422,
    parse_range,
)
from onchain_platform.research.api.schemas import PaginatedResponse

router = APIRouter()


@router.get(
    "/entities/{entity_id:path}/snapshots",
    summary="Observation snapshots for an entity",
    description="Historical ObservationSnapshot recordings for an entity, "
    "cursor-paginated in chronological order (DOC-015 Endpoint Catalog).",
    response_model=PaginatedResponse[ObservationSnapshot],
)
async def list_snapshots(
    entity_id: str,
    start: str | None = Query(default=None, description="ISO-8601 inclusive start"),
    end: str | None = Query(default=None, description="ISO-8601 inclusive end"),
    cursor: str | None = Query(default=None, description="Opaque pagination cursor"),
    limit: int = Query(default=100, ge=1, le=1000, description="Max items per page (<=1000)"),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[ObservationSnapshot]:
    canonical = unquote(entity_id)
    if not canonical.startswith("eip155:"):
        raise HTTPException(status_code=422, detail="entity_id must be a canonical ID")

    start_dt, end_dt = parse_range(start, end)
    items, next_keys = await ts_repo.list_snapshots_page(
        session,
        canonical,
        start=start_dt,
        end=end_dt,
        cursor=decode_cursor_or_422(cursor),
        limit=limit,
    )
    return build_page(items, next_keys)
