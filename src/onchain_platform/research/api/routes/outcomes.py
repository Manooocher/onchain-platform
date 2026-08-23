"""Outcome endpoints (DOC-015 Endpoint Catalog).

`/v1/entities/{id}/outcomes` — Outcome (ground truth) history, cursor-paginated.
"""

from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.enums import OutcomeType
from onchain_platform.domain.schemas.outcome import Outcome
from onchain_platform.persistence.postgres.outcomes_insights import list_outcomes_page
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.routes._common import build_page, decode_cursor_or_422
from onchain_platform.research.api.schemas import PaginatedResponse

router = APIRouter()


@router.get(
    "/entities/{entity_id:path}/outcomes",
    summary="Outcomes for an entity",
    description="Outcome (ground-truth label) history for an entity with "
    "optional type filter, cursor-paginated newest-first (DOC-015 Endpoint "
    "Catalog).",
    response_model=PaginatedResponse[Outcome],
)
async def list_outcomes(
    entity_id: str,
    outcome_type: OutcomeType | None = Query(default=None, description="Filter by outcome type"),
    cursor: str | None = Query(default=None, description="Opaque pagination cursor"),
    limit: int = Query(default=100, ge=1, le=1000, description="Max items per page (<=1000)"),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[Outcome]:
    canonical = unquote(entity_id)
    if not canonical.startswith("eip155:"):
        raise HTTPException(status_code=422, detail="entity_id must be a canonical ID")

    items, next_keys = await list_outcomes_page(
        session,
        canonical,
        outcome_type=outcome_type,
        cursor=decode_cursor_or_422(cursor),
        limit=limit,
    )
    return build_page(items, next_keys)
