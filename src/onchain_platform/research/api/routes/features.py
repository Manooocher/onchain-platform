"""Point-in-Time Feature endpoints (DOC-015 § The Point-in-Time Query Pattern).

`/v1/entities/{id}/features/{name}?as_of=` — single feature PIT lookup.
`/v1/entities/{id}/features?as_of=`       — all features PIT (latest-per-name).

`as_of` defaults to the current server time (the API boundary is allowed the
wall clock — DOC-015 § PIT; underlying repo reads never read it). A missing
value is a 404, never an empty 200 (DOC-015).
"""

from datetime import UTC, datetime
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.persistence.timescale import repositories as ts_repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.routes._common import build_page
from onchain_platform.research.api.schemas import PaginatedResponse

router = APIRouter()


def _resolve_as_of(as_of: str | None) -> datetime:
    """Parse as_of, defaulting to current server time when omitted (DOC-015)."""
    if as_of is None:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="malformed as_of") from exc


@router.get(
    "/entities/{entity_id:path}/features/{feature_name}",
    summary="Get a feature as of a time",
    description="Most recent Feature of a given name whose as_of_timestamp "
    "is <= the requested as_of (Point-in-Time; DOC-015). as_of defaults to "
    "now. 404 if no Feature satisfies the filter (not an empty 200).",
    response_model=Feature,
)
async def get_feature(
    entity_id: str,
    feature_name: str,
    as_of: str | None = Query(default=None, description="ISO-8601 point-in-time"),
    session: AsyncSession = Depends(get_session),
) -> Feature:
    canonical = unquote(entity_id)
    if not canonical.startswith("eip155:"):
        raise HTTPException(status_code=422, detail="entity_id must be a canonical ID")

    feature = await ts_repo.get_feature_at(session, canonical, feature_name, _resolve_as_of(as_of))
    if feature is None:
        raise HTTPException(status_code=404, detail="Feature not found")
    return feature


@router.get(
    "/entities/{entity_id:path}/features",
    summary="All features for an entity as of a time",
    description="Every feature_name known for an entity, each resolved to its "
    "most recent-as-of-as_of row (latest-per-name, Point-in-Time; DOC-015). "
    "as_of defaults to now.",
    response_model=PaginatedResponse[Feature],
)
async def list_features(
    entity_id: str,
    as_of: str | None = Query(default=None, description="ISO-8601 point-in-time"),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[Feature]:
    canonical = unquote(entity_id)
    if not canonical.startswith("eip155:"):
        raise HTTPException(status_code=422, detail="entity_id must be a canonical ID")

    as_of_dt = _resolve_as_of(as_of)
    latest = await ts_repo.list_latest_features(session, canonical, as_of_dt)
    # Deterministic order (list_latest_features sorts by name) — no cursor
    # (DOC-015 exposes no pagination for the features PIT form).
    return build_page(latest, None)
