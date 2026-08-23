"""Market Bar endpoints (DOC-015 Endpoint Catalog).

`/v1/pairs/{id}/bars` — OHLCV history, `interval` required, cursor-paginated.
"""

from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.enums import BarInterval
from onchain_platform.domain.schemas.market_bar import MarketBar
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
    "/pairs/{pair_id:path}/bars",
    summary="Market bars for a pair",
    description="OHLCV history for a pair at one interval, cursor-paginated "
    "(DOC-015 Endpoint Catalog). include_provisional defaults false — "
    "provisional bars are never for research datasets (DOC-012 § B.3).",
    response_model=PaginatedResponse[MarketBar],
)
async def list_pair_bars(
    pair_id: str,
    interval: BarInterval = Query(..., description="Bar interval"),
    start: str | None = Query(default=None, description="ISO-8601 inclusive start"),
    end: str | None = Query(default=None, description="ISO-8601 inclusive end"),
    include_provisional: bool = Query(default=False, description="Include provisional bars"),
    cursor: str | None = Query(default=None, description="Opaque pagination cursor"),
    limit: int = Query(default=100, ge=1, le=1000, description="Max items per page (<=1000)"),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[MarketBar]:
    canonical = unquote(pair_id)
    # pair_id is the canonical TradingPair ID (eip155:8453/pair:0x...).
    if not canonical.startswith("eip155:"):
        raise HTTPException(status_code=422, detail="pair_id must be a canonical ID")

    start_dt, end_dt = parse_range(start, end)
    items, next_keys = await ts_repo.list_bars_page(
        session,
        canonical,
        interval,
        start=start_dt,
        end=end_dt,
        include_provisional=include_provisional,
        cursor=decode_cursor_or_422(cursor),
        limit=limit,
    )
    return build_page(items, next_keys)
