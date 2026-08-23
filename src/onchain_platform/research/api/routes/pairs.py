"""TradingPair endpoints (DOC-015 Resource Model).

`/v1/pairs` — discover/list with filters + cursor pagination.
`/v1/pairs/{id}` — single pair with nested LiquidityPool + Metadata.
"""

from datetime import UTC, datetime
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.persistence.postgres import entity_repositories as repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.routes._common import build_page, decode_cursor_or_422
from onchain_platform.research.api.schemas import (
    PaginatedResponse,
    PairDetail,
)

router = APIRouter()


@router.get(
    "/pairs",
    summary="List trading pairs",
    description="Discover trading pairs with optional chain_id/dex filters and "
    "cursor pagination (DOC-015 Endpoint Catalog). Responses are TradingPair "
    "Canonical Schemas.",
    response_model=PaginatedResponse[TradingPair],
)
async def list_pairs(
    chain_id: int | None = Query(default=None, description="EIP-155 chain id"),
    dex: str | None = Query(default=None, description="DEX label, e.g. uniswap_v2"),
    created_after: str | None = Query(default=None, description="ISO-8601 creation cutoff"),
    cursor: str | None = Query(default=None, description="Opaque pagination cursor"),
    limit: int = Query(default=100, ge=1, le=1000, description="Max items per page (<=1000)"),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[TradingPair]:
    created_after_dt = None
    if created_after is not None:
        try:
            created_after_dt = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
            if created_after_dt.tzinfo is None:
                created_after_dt = created_after_dt.replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="malformed created_after") from exc

    items, next_keys = await repo.list_pairs(
        session,
        chain_id=chain_id,
        dex=dex,
        created_after=created_after_dt,
        cursor=decode_cursor_or_422(cursor),
        limit=limit,
    )
    return build_page(items, next_keys)


@router.get(
    "/pairs/{pair_id:path}",
    summary="Get a trading pair",
    description="Single TradingPair with its nested LiquidityPool and Metadata "
    "(DOC-015 Resource Model). 404 if the pair does not exist. The canonical "
    "ID may contain '/' — hence the :path converter.",
    response_model=PairDetail,
)
async def get_pair(
    pair_id: str,
    session: AsyncSession = Depends(get_session),
) -> PairDetail:
    pair = await repo.get_trading_pair(session, unquote(pair_id))
    if pair is None:
        raise HTTPException(status_code=404, detail="Trading pair not found")
    canonical = unquote(pair_id)
    liquidity_pool = await repo.get_liquidity_pool(session, canonical)
    metadata = await repo.get_metadata(session, canonical)
    return PairDetail(pair=pair, liquidity_pool=liquidity_pool, metadata=metadata)
