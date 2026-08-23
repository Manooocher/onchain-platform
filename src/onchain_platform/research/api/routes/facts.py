"""Facts endpoints (DOC-015 Endpoint Catalog).

`/v1/facts/{fact_id}` — single Fact by natural key.
`/v1/pairs/{id}/facts` — Raw Facts for a pair (audit/research), with
fact_type/start/end/include_unfinalized filters and cursor pagination.
"""

from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.blockchain_fact import BlockchainFact
from onchain_platform.domain.schemas.enums import FactType
from onchain_platform.persistence.postgres import repositories as facts_repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.routes._common import (
    build_page,
    decode_cursor_or_422,
    parse_range,
)
from onchain_platform.research.api.schemas import PaginatedResponse

router = APIRouter()


@router.get(
    "/facts/{fact_id:path}",
    summary="Get a fact",
    description="Single BlockchainFact by its natural key (DOC-015 Endpoint "
    "Catalog). 404 if it does not exist.",
    response_model=BlockchainFact,
)
async def get_fact(
    fact_id: str,
    session: AsyncSession = Depends(get_session),
) -> BlockchainFact:
    fact = await facts_repo.get_fact(session, unquote(fact_id))
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    return fact


@router.get(
    "/pairs/{pair_id:path}/facts",
    summary="List facts for a pair",
    description="Raw Facts referencing a pair, filtered by type/range/finality, "
    "cursor-paginated (DOC-015 Endpoint Catalog). Only FINALIZED facts unless "
    "include_unfinalized=true.",
    response_model=PaginatedResponse[BlockchainFact],
)
async def list_pair_facts(
    pair_id: str,
    fact_type: FactType | None = Query(default=None, description="Filter by fact type"),
    start: str | None = Query(default=None, description="ISO-8601 inclusive start"),
    end: str | None = Query(default=None, description="ISO-8601 inclusive end"),
    include_unfinalized: bool = Query(
        default=False, description="Include non-orphaned unfinalized"
    ),
    cursor: str | None = Query(default=None, description="Opaque pagination cursor"),
    limit: int = Query(default=100, ge=1, le=1000, description="Max items per page (<=1000)"),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[BlockchainFact]:
    canonical = unquote(pair_id)
    # Extract chain_id + pool_address from canonical ID eip155:<chain>/pair:<addr>.
    if not canonical.startswith("eip155:"):
        raise HTTPException(status_code=422, detail="pair_id must be a canonical ID")
    chain_part, _, entity = canonical.removeprefix("eip155:").partition("/")
    if not entity.startswith("pair:"):
        raise HTTPException(status_code=422, detail="pair_id must reference a pair")
    pool_address = entity.split(":", 1)[1]
    try:
        chain_id = int(chain_part)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="malformed pair canonical ID") from exc

    start_dt, end_dt = parse_range(start, end)
    items, next_keys = await facts_repo.list_facts_for_pair(
        session,
        chain_id,
        pool_address,
        fact_type=fact_type,
        start=start_dt,
        end=end_dt,
        include_unfinalized=include_unfinalized,
        cursor=decode_cursor_or_422(cursor),
        limit=limit,
    )
    return build_page(items, next_keys)
