"""Integration tests: cursor pagination stability (Phase C).

Verifies keyset cursor pagination across the collection endpoints returns no
duplicates and no gaps, and items are correctly ordered. Runs against real
Postgres/TimescaleDB via the app session override (DOC-010 § Integration
Tests).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013).
"""

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from eth_utils.address import to_checksum_address
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.entities.liquidity_pool import LiquidityPool
from onchain_platform.domain.entities.metadata import Metadata
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import (
    BarInterval,
    ConfirmationStatus,
    FactType,
    Importance,
    OutcomeType,
)
from onchain_platform.domain.schemas.insight import Insight
from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.domain.schemas.outcome import Outcome
from onchain_platform.persistence.postgres import entity_repositories as entity_repos
from onchain_platform.persistence.postgres import repositories as facts_repo
from onchain_platform.persistence.postgres.outcomes_insights import save_insight, save_outcome
from onchain_platform.persistence.timescale import repositories as ts_repos
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.main import create_app

CHAIN_ID = 8453
# The seed creates three pairs whose pool addresses are built from the bytes
# 33/34/35 (i.e. hex 21/22/23), plus a distinct test pair address for the
# entity-scoped endpoints. Have all three collection tests target the SAME
# first seeded pair, so bars/snapshots/facts have real content.
FIRST_BYTE = 33  # decimal → hex "21"
POOL = to_checksum_address("0x" + format(FIRST_BYTE, "02x") * 20)
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
TOKEN1 = to_checksum_address("0x" + "44" * 20)
WALLET = to_checksum_address("0x" + "99" * 20)
ENTITY_ID = pair_canonical_id(CHAIN_ID, POOL)
T0 = datetime(2026, 8, 21, 8, 0, 0, tzinfo=UTC)


def _make_client(pg_engine: AsyncEngine) -> httpx.AsyncClient:
    from httpx import ASGITransport

    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_rich_data(session: AsyncSession) -> None:
    """Seed 3 pairs, several bars/snapshots/facts/outcomes/insights so
    pagination across pages has real content."""
    await entity_repos.save_token(
        session,
        Token(
            canonical_id=token_canonical_id(CHAIN_ID, TOKEN0),
            chain_id=CHAIN_ID,
            contract_address=TOKEN0,
        ),
    )
    await entity_repos.save_token(
        session,
        Token(
            canonical_id=token_canonical_id(CHAIN_ID, TOKEN1),
            chain_id=CHAIN_ID,
            contract_address=TOKEN1,
        ),
    )
    for byte in (33, 34, 35):
        pool = to_checksum_address("0x" + format(byte, "02x") * 20)
        eid = pair_canonical_id(CHAIN_ID, pool)
        await entity_repos.save_trading_pair(
            session,
            TradingPair(
                canonical_id=eid,
                chain_id=CHAIN_ID,
                dex="uniswap_v2",
                base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
                quote_token_id=token_canonical_id(CHAIN_ID, TOKEN1),
                pool_address=pool,
                creation_block=100 + byte,
                creation_fact_id=f"{CHAIN_ID}:0x{format(byte, '02x') * 32}:0",
            ),
        )
        await entity_repos.save_liquidity_pool(
            session, LiquidityPool(canonical_id=eid, protocol="uniswap_v2")
        )
        await entity_repos.save_metadata(session, Metadata(entity_id=eid, last_updated=T0))
        # SWAP facts referencing the pair (for /pairs/{id}/facts).
        for i in range(4):
            tx = "0x" + (f"{byte:02x}{i:02x}" * 16).lower()
            await facts_repo.save_fact(
                session,
                BlockchainFact(
                    schema_version="1.0",
                    fact_id=f"{CHAIN_ID}:{tx}:0",
                    chain_id=CHAIN_ID,
                    fact_type=FactType.SWAP_EXECUTED,
                    block_number=100 + byte,
                    block_hash="0x" + format(byte, "02x") * 32,
                    tx_hash=tx,
                    log_index=0,
                    event_time=T0 + timedelta(minutes=i),
                    observed_at=T0 + timedelta(minutes=i),
                    ingested_at=T0 + timedelta(minutes=i),
                    confirmation_status=ConfirmationStatus.FINALIZED,
                    confirmations=10,
                    payload=SwapExecutedPayload(
                        fact_type="SWAP_EXECUTED",
                        pool_address=pool,
                        sender=WALLET,
                        recipient=TOKEN1,
                        amount0_in="100",
                        amount1_in="0",
                        amount0_out="0",
                        amount1_out="200",
                    ),
                ),
            )
        # Bars (1h interval) + snapshots for entity.
        for i in range(5):
            ts = T0 + timedelta(hours=i)
            await ts_repos.save_bar(
                session,
                MarketBar.create(
                    pair_id=eid,
                    chain_id=CHAIN_ID,
                    interval=BarInterval.ONE_HOUR,
                    bar_start_time=ts,
                    open_="1",
                    high="1",
                    low="1",
                    close="1",
                    volume_base="0",
                    volume_quote="0",
                    trade_count=1,
                    vwap="1",
                    buy_volume="0",
                    sell_volume="0",
                    source_fact_range=("f", "f"),
                    computed_at=T0,
                ),
            )
            await ts_repos.save_snapshot(
                session,
                ObservationSnapshot.create(
                    entity_id=eid,
                    chain_id=CHAIN_ID,
                    snapshot_timestamp=ts,
                    observed_at=ts,
                    ingested_at=ts,
                    source="test",
                    reserve0="100",
                    reserve1="100",
                    price="1",
                ),
            )
        await save_outcome(
            session,
            Outcome.create(
                entity_id=eid,
                outcome_type=OutcomeType.SUCCESSFUL_LAUNCH,
                observation_window="1h",
                label_definition="launch",
                label_definition_version="1.0",
                evaluation_timestamp=T0 + timedelta(hours=2),
                evaluated_at=T0 + timedelta(hours=2),
                label_value=True,
            ),
        )
        await save_insight(
            session,
            Insight(
                insight_id=f"{eid}|HoneypotDetected|{T0.isoformat()}",
                entity_id=eid,
                insight_type="HoneypotDetected",
                summary="x",
                generated_at=T0,
                source_features=[],
                importance=Importance.HIGH,
            ),
        )


async def _wipe(pg_engine: AsyncEngine) -> None:
    from sqlalchemy import text

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE outcomes, insights, observation_snapshots, market_bars, features, "
                "blockchain_facts, metadata, smart_contracts, liquidity_pools, "
                "trading_pairs, tokens, wallets CASCADE"
            )
        )


async def test_pairs_pagination_no_duplicate_no_gap(
    pg_engine: AsyncEngine, clean_entities: Callable, clean_facts: Callable
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_rich_data(session)

    all_ids: list[str] = []
    cursor: str | None = None
    async with _make_client(pg_engine) as client:
        while True:
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get("/v1/pairs", params=params)
            assert resp.status_code == 200
            data = resp.json()
            all_ids.extend(item["canonical_id"] for item in data["items"])
            if not data["pagination"]["has_more"]:
                break
            cursor = data["pagination"]["next_cursor"]

    assert len(all_ids) == 3  # exactly the 3 seeded pairs
    assert len(set(all_ids)) == 3  # no duplicates


async def test_bars_pagination_sorted_and_filtered(
    pg_engine: AsyncEngine, clean_entities: Callable, clean_facts: Callable
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_rich_data(session)

    qid = quote(ENTITY_ID, safe="")
    all_start: list[str] = []
    cursor: str | None = None
    async with _make_client(pg_engine) as client:
        while True:
            params = {"interval": "1h", "limit": 2}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(f"/v1/pairs/{qid}/bars", params=params)
            assert resp.status_code == 200
            data = resp.json()
            all_start.extend(item["bar_start_time"] for item in data["items"])
            if not data["pagination"]["has_more"]:
                break
            cursor = data["pagination"]["next_cursor"]

    # 5 bars, ascending by bar_start_time, no duplicates.
    assert len(all_start) == 5
    assert all_start == sorted(all_start)
    assert len(set(all_start)) == 5


async def test_snapshots_pagination_sorted(
    pg_engine: AsyncEngine, clean_entities: Callable, clean_facts: Callable
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_rich_data(session)

    qid = quote(ENTITY_ID, safe="")
    all_ts: list[str] = []
    cursor: str | None = None
    async with _make_client(pg_engine) as client:
        while True:
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(f"/v1/entities/{qid}/snapshots", params=params)
            assert resp.status_code == 200
            data = resp.json()
            all_ts.extend(item["snapshot_timestamp"] for item in data["items"])
            if not data["pagination"]["has_more"]:
                break
            cursor = data["pagination"]["next_cursor"]

    assert len(all_ts) == 5
    assert all_ts == sorted(all_ts)
    assert len(set(all_ts)) == 5


async def test_facts_pagination_with_type_filter(
    pg_engine: AsyncEngine, clean_entities: Callable, clean_facts: Callable
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_rich_data(session)

    qid = quote(ENTITY_ID, safe="")
    types: list[str] = []
    cursor: str | None = None
    async with _make_client(pg_engine) as client:
        while True:
            params = {"fact_type": "SWAP_EXECUTED", "limit": 2}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(f"/v1/pairs/{qid}/facts", params=params)
            assert resp.status_code == 200
            data = resp.json()
            items = data["items"]
            types.extend(item["fact_type"] for item in items)
            if not data["pagination"]["has_more"]:
                break
            cursor = data["pagination"]["next_cursor"]

    assert len(types) == 4, "should be 4 SWAP facts for the pair"
    assert all(t == "SWAP_EXECUTED" for t in types)


async def test_wallet_activity_pagination(
    pg_engine: AsyncEngine, clean_entities: Callable, clean_facts: Callable
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_rich_data(session)

    wid = quote(f"eip155:{CHAIN_ID}/wallet:{WALLET}", safe="")
    items: list[str] = []
    cursor: str | None = None
    async with _make_client(pg_engine) as client:
        while True:
            params = {"limit": 5}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(f"/v1/wallets/{wid}/activity", params=params)
            assert resp.status_code == 200
            data = resp.json()
            items.extend(item["fact_id"] for item in data["items"])
            if not data["pagination"]["has_more"]:
                break
            cursor = data["pagination"]["next_cursor"]

    assert len(items) >= 1
    assert len(set(items)) == len(items), "wallet activity has duplicates"
