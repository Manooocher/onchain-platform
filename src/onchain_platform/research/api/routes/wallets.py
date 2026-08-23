"""Wallet endpoints (DOC-015 Resource Model).

`/v1/wallets/{id}` — single Wallet.
`/v1/wallets/{id}/activity` — Wallet Activity: Facts filtered by wallet
involvement (DOC-015 Endpoint Catalog), cursor-paginated.

The `{wallet_id}` in both routes uses the `:path` converter so canonical IDs
(which contain `/`, e.g. `eip155:8453/wallet:0x...`) are captured fully.
The `/activity` sub-resource is declared BEFORE the plain `{wallet_id:path}`
route so `/wallets/<id>/activity` wins over a bare wallet capture.
"""

from datetime import UTC, datetime
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.blockchain_fact import BlockchainFact
from onchain_platform.persistence.postgres import entity_repositories as repo
from onchain_platform.persistence.postgres import repositories as facts_repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.routes._common import build_page, decode_cursor_or_422
from onchain_platform.research.api.schemas import (
    PaginatedResponse,
    WalletDetail,
)

router = APIRouter()


@router.get(
    "/wallets/{wallet_id:path}/activity",
    summary="Wallet activity",
    description="Blockchain Facts involving this wallet (DOC-015 Endpoint "
    "Catalog). Wallet Activity consists entirely of Facts; cursor-paginated. "
    "The canonical ID may contain '/' — hence the :path converter.",
    response_model=PaginatedResponse[BlockchainFact],
)
async def get_wallet_activity(
    wallet_id: str,
    start: str | None = Query(default=None, description="ISO-8601 inclusive start"),
    end: str | None = Query(default=None, description="ISO-8601 inclusive end"),
    cursor: str | None = Query(default=None, description="Opaque pagination cursor"),
    limit: int = Query(default=100, ge=1, le=1000, description="Max items per page (<=1000)"),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[BlockchainFact]:
    chain_id, address = _parse_wallet_id(unquote(wallet_id))

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

    items, next_keys = await facts_repo.list_facts_for_wallet(
        session,
        chain_id,
        address,
        start=start_dt,
        end=end_dt,
        cursor=decode_cursor_or_422(cursor),
        limit=limit,
    )
    return build_page(items, next_keys)


@router.get(
    "/wallets/{wallet_id:path}",
    summary="Get a wallet",
    description="Single Wallet entity (DOC-015 Resource Model). 404 if it "
    "does not exist. The canonical ID may contain '/' — hence the :path "
    "converter.",
    response_model=WalletDetail,
)
async def get_wallet(
    wallet_id: str,
    session: AsyncSession = Depends(get_session),
) -> WalletDetail:
    wallet = await repo.get_wallet(session, unquote(wallet_id))
    if wallet is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return WalletDetail(wallet=wallet)


def _parse_wallet_id(wallet_id: str) -> tuple[int, str]:
    """Canonical ID eip155:<chain_id>/wallet:<address> → (chain_id, address)."""
    if not wallet_id.startswith("eip155:"):
        raise HTTPException(status_code=422, detail="wallet_id must be a canonical ID")
    _prefix, _, chain_and_entity = wallet_id.partition(":")
    chain_part, _, entity_and_addr = chain_and_entity.partition("/")
    if not entity_and_addr.startswith("wallet:"):
        raise HTTPException(status_code=422, detail="wallet_id must reference a wallet")
    address = entity_and_addr.split(":", 1)[1]
    try:
        chain_id = int(chain_part)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="malformed wallet canonical ID") from exc
    return chain_id, address
