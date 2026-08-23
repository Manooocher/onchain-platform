"""Integration tests: Entity & Pair endpoints (Phase B).

Runs against real Postgres via the app's session dependency, overridden to
use the test's pg_engine fixture so everything shares one event loop
(DOC-010 § Integration Tests, DOC-013 § Testing Conventions).

Verifies pair listing (filters + pagination), nested pair/token detail, and
wallet activity. Canonical IDs are percent-encoded in paths per DOC-015.
Naming: test_<unit>_<scenario>_<expected_outcome>.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from eth_utils.address import to_checksum_address
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.entities.liquidity_pool import LiquidityPool
from onchain_platform.domain.entities.metadata import Metadata
from onchain_platform.domain.entities.smart_contract import SmartContract
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.enums import ContractType
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType
from onchain_platform.persistence.postgres import entity_repositories as entity_repos
from onchain_platform.persistence.postgres import repositories as facts_repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.main import create_app

CHAIN_ID = 8453
POOL = to_checksum_address("0x" + "55" * 20)
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
TOKEN1 = to_checksum_address("0x" + "66" * 20)
WALLET = to_checksum_address("0x" + "11" * 20)
ENTITY_ID = pair_canonical_id(CHAIN_ID, POOL)
TOKEN_ID = token_canonical_id(CHAIN_ID, TOKEN0)
WALLET_ID = f"eip155:{CHAIN_ID}/wallet:{WALLET}"
T0 = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
PINNED = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)

_CleanFn = Callable[[], Awaitable[None]]


def _client(pg_engine: AsyncEngine) -> httpx.AsyncClient:
    """Build the app with get_session overridden to use the test engine, and
    return an httpx AsyncClient via ASGITransport (same event loop)."""
    from httpx import ASGITransport

    app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed(session: AsyncSession) -> None:
    await entity_repos.save_token(
        session,
        Token(canonical_id=TOKEN_ID, chain_id=CHAIN_ID, contract_address=TOKEN0),
    )
    await entity_repos.save_token(
        session,
        Token(
            canonical_id=token_canonical_id(CHAIN_ID, TOKEN1),
            chain_id=CHAIN_ID,
            contract_address=TOKEN1,
        ),
    )
    await entity_repos.save_trading_pair(
        session,
        TradingPair(
            canonical_id=ENTITY_ID,
            chain_id=CHAIN_ID,
            dex="uniswap_v2",
            base_token_id=TOKEN_ID,
            quote_token_id=token_canonical_id(CHAIN_ID, TOKEN1),
            pool_address=POOL,
            creation_block=100,
            creation_fact_id=f"{CHAIN_ID}:0x{'aa' * 32}:0",
        ),
    )
    await entity_repos.save_liquidity_pool(
        session, LiquidityPool(canonical_id=ENTITY_ID, protocol="uniswap_v2", fee_tier_bps=30)
    )
    await entity_repos.save_smart_contract(
        session,
        SmartContract(
            canonical_id=f"eip155:{CHAIN_ID}/contract:{TOKEN0}",
            chain_id=CHAIN_ID,
            address=TOKEN0,
            contract_type=ContractType.ERC20,
        ),
    )
    await entity_repos.save_metadata(
        session,
        Metadata(entity_id=ENTITY_ID, website="https://example.com", last_updated=PINNED),
    )
    await facts_repo.save_fact(
        session,
        BlockchainFact(
            schema_version="1.0",
            fact_id=f"{CHAIN_ID}:0x{'bb' * 32}:0",
            chain_id=CHAIN_ID,
            fact_type=FactType.SWAP_EXECUTED,
            block_number=101,
            block_hash="0x" + "22" * 32,
            tx_hash="0x" + "bb" * 32,
            log_index=0,
            event_time=T0,
            observed_at=T0,
            ingested_at=T0,
            confirmation_status=ConfirmationStatus.FINALIZED,
            confirmations=10,
            payload=SwapExecutedPayload(
                fact_type="SWAP_EXECUTED",
                pool_address=POOL,
                sender=WALLET,
                recipient=TOKEN1,
                amount0_in="100",
                amount1_in="0",
                amount0_out="0",
                amount1_out="200",
            ),
        ),
    )


async def test_get_pair_with_nested_resources(
    pg_engine: AsyncEngine, clean_entities: _CleanFn, clean_facts: _CleanFn
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed(session)

    qid = quote(ENTITY_ID, safe="")
    async with _client(pg_engine) as client:
        resp = await client.get(f"/v1/pairs/{qid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pair"]["canonical_id"] == ENTITY_ID
    assert body["liquidity_pool"]["protocol"] == "uniswap_v2"
    assert body["metadata"]["website"] == "https://example.com"


async def test_get_pair_missing_404(
    pg_engine: AsyncEngine, clean_entities: _CleanFn, clean_facts: _CleanFn
) -> None:
    await clean_entities()
    await clean_facts()
    qid = quote("eip155:8453/pair:0x" + "00" * 20, safe="")
    async with _client(pg_engine) as client:
        resp = await client.get(f"/v1/pairs/{qid}")
    assert resp.status_code == 404
    assert "error" in resp.json()


async def test_get_token_with_nested_resources(
    pg_engine: AsyncEngine, clean_entities: _CleanFn, clean_facts: _CleanFn
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed(session)

    qid = quote(TOKEN_ID, safe="")
    async with _client(pg_engine) as client:
        resp = await client.get(f"/v1/tokens/{qid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]["canonical_id"] == TOKEN_ID
    assert body["smart_contract"]["contract_type"] == "ERC20"


async def test_list_pairs_filters_and_pagination(
    pg_engine: AsyncEngine, clean_entities: _CleanFn, clean_facts: _CleanFn
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed(session)

    async with _client(pg_engine) as client:
        resp = await client.get("/v1/pairs", params={"chain_id": CHAIN_ID, "dex": "uniswap_v2"})
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "pagination" in body
    assert len(body["items"]) >= 1
    assert body["items"][0]["chain_id"] == CHAIN_ID


async def test_get_wallet_activity(
    pg_engine: AsyncEngine, clean_entities: _CleanFn, clean_facts: _CleanFn
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed(session)

    qid = quote(WALLET_ID, safe="")
    async with _client(pg_engine) as client:
        resp = await client.get(f"/v1/wallets/{qid}/activity")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) >= 1
    assert body["items"][0]["fact_type"] == "SWAP_EXECUTED"
