"""Integration tests: Persistence Read Layer (Phase 0) — the readers that
back the Research Platform API (DOC-015).

Everything runs against real Postgres/TimescaleDB (DOC-010 § Integration
Tests). Covers keyset pagination (no dup/gap), JSONB fact filtering,
GIN wallet activity, and nested entity reads.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

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
    PairCreatedPayload,
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
from onchain_platform.persistence.postgres import (
    entity_repositories as entity_repos,
)
from onchain_platform.persistence.postgres import (
    repositories as fact_repos,
)
from onchain_platform.persistence.postgres.outcomes_insights import (
    list_insights_page,
    list_outcomes_page,
    save_insight,
    save_outcome,
)
from onchain_platform.persistence.timescale import repositories as ts_repos

CHAIN_ID = 8453
# A distinct pair address per test file to avoid cross-test pollution.
POOL = to_checksum_address("0x" + "77" * 20)
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
TOKEN1 = to_checksum_address("0x" + "88" * 20)
SENDER = to_checksum_address("0x" + "11" * 20)
ENTITY_ID = pair_canonical_id(CHAIN_ID, POOL)
T0 = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
PINNED = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)

_CleanFn = Callable[[], Awaitable[None]]


def _pair_created_fact(tx: str, event_time: datetime) -> BlockchainFact:
    return BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:{tx}:0",
        chain_id=CHAIN_ID,
        fact_type=FactType.PAIR_CREATED,
        block_number=100,
        block_hash="0x" + "11" * 32,
        tx_hash=tx,
        log_index=0,
        event_time=event_time,
        observed_at=event_time,
        ingested_at=event_time,
        confirmation_status=ConfirmationStatus.FINALIZED,
        confirmations=10,
        payload=PairCreatedPayload(
            fact_type="PAIR_CREATED",
            pair_address=POOL,
            token0_address=TOKEN0,
            token1_address=TOKEN1,
            dex="uniswap_v2",
        ),
    )


def _swap_fact(tx: str, event_time: datetime) -> BlockchainFact:
    return BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:{tx}:0",
        chain_id=CHAIN_ID,
        fact_type=FactType.SWAP_EXECUTED,
        block_number=101,
        block_hash="0x" + "22" * 32,
        tx_hash=tx,
        log_index=0,
        event_time=event_time,
        observed_at=event_time,
        ingested_at=event_time,
        confirmation_status=ConfirmationStatus.FINALIZED,
        confirmations=10,
        payload=SwapExecutedPayload(
            fact_type="SWAP_EXECUTED",
            pool_address=POOL,
            sender=SENDER,
            recipient=TOKEN1,
            amount0_in="100",
            amount1_in="0",
            amount0_out="0",
            amount1_out="200",
        ),
    )


async def _seed_pair_and_entities(
    session: AsyncSession, created_at: datetime = T0, creation_fact_id: str | None = None
) -> None:
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
    await entity_repos.save_trading_pair(
        session,
        TradingPair(
            canonical_id=ENTITY_ID,
            chain_id=CHAIN_ID,
            dex="uniswap_v2",
            base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
            quote_token_id=token_canonical_id(CHAIN_ID, TOKEN1),
            pool_address=POOL,
            creation_block=100,
            creation_fact_id=creation_fact_id or f"{CHAIN_ID}:0x{'aa' * 32}:0",
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


async def _clean_all(pg_engine: AsyncEngine) -> None:
    from sqlalchemy import text

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE outcomes, insights, observation_snapshots, market_bars, features, "
                "blockchain_facts, metadata, smart_contracts, liquidity_pools, "
                "trading_pairs, tokens, wallets CASCADE"
            )
        )


# ---------------------------------------------------------------------------
# Nested entity readers
# ---------------------------------------------------------------------------


async def test_get_liquidity_pool_and_metadata_and_smart_contract(
    pg_engine: AsyncEngine, clean_entities: _CleanFn
) -> None:
    """The three new nested readers return seeded entities."""
    await clean_entities()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_pair_and_entities(session)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        pool = await entity_repos.get_liquidity_pool(session, ENTITY_ID)
        meta = await entity_repos.get_metadata(session, ENTITY_ID)
        sc = await entity_repos.get_smart_contract(session, f"eip155:{CHAIN_ID}/contract:{TOKEN0}")

    assert pool is not None
    assert pool.protocol == "uniswap_v2"
    assert pool.fee_tier_bps == 30
    assert meta is not None
    assert meta.website == "https://example.com"
    assert sc is not None
    assert sc.contract_type == ContractType.ERC20


async def test_get_metadata_missing_returns_none(
    pg_engine: AsyncEngine, clean_entities: _CleanFn
) -> None:
    await clean_entities()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        assert await entity_repos.get_metadata(session, ENTITY_ID) is None


# ---------------------------------------------------------------------------
# list_pairs — filters + keyset pagination
# ---------------------------------------------------------------------------


async def test_list_pairs_keyset_pagination_no_duplicates(
    pg_engine: AsyncEngine, clean_entities: _CleanFn, clean_facts: _CleanFn
) -> None:
    await clean_entities()
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        # Seed three pairs (different addresses) so pagination has > 1 page.
        for byte in (77, 78, 79):
            pool = to_checksum_address("0x" + format(byte, "02x") * 20)
            eid = pair_canonical_id(CHAIN_ID, pool)
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
            await entity_repos.save_trading_pair(
                session,
                TradingPair(
                    canonical_id=eid,
                    chain_id=CHAIN_ID,
                    dex="uniswap_v2",
                    base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
                    quote_token_id=token_canonical_id(CHAIN_ID, TOKEN1),
                    pool_address=pool,
                    creation_block=100,
                    creation_fact_id=f"{CHAIN_ID}:0x{format(byte, '02x') * 32}:0",
                ),
            )

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        page1, cursor1 = await entity_repos.list_pairs(session, chain_id=CHAIN_ID, limit=1)
        page2, cursor2 = await entity_repos.list_pairs(
            session, chain_id=CHAIN_ID, limit=1, cursor=cursor1
        )
        page3, cursor3 = await entity_repos.list_pairs(
            session, chain_id=CHAIN_ID, limit=1, cursor=cursor2
        )

    all_cids = [p.canonical_id for p in page1 + page2 + page3]
    assert len(all_cids) == 3
    assert len(set(all_cids)) == 3  # no duplicates across pages
    assert cursor3 is None  # exhausted


async def test_list_pairs_filter_dex_and_missing(
    pg_engine: AsyncEngine, clean_entities: _CleanFn
) -> None:
    await clean_entities()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_pair_and_entities(session)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        matched, _ = await entity_repos.list_pairs(session, dex="uniswap_v2")
        none_match, _ = await entity_repos.list_pairs(session, dex="aerodrome")
    assert len(matched) == 1
    assert none_match == []


# ---------------------------------------------------------------------------
# list_facts_for_pair — JSONB filter
# ---------------------------------------------------------------------------


async def test_list_facts_for_pair_jsonb_filter(
    pg_engine: AsyncEngine, clean_facts: _CleanFn
) -> None:
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await fact_repos.save_fact(session, _pair_created_fact("0x" + "aa" * 32, T0))
        await fact_repos.save_fact(session, _swap_fact("0x" + "bb" * 32, T0))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        items, cursor = await fact_repos.list_facts_for_pair(session, CHAIN_ID, POOL, limit=10)
    assert len(items) == 2  # both the PAIR_CREATED (pair_address) and SWAP (pool_address)
    assert cursor is None
    assert all(f.chain_id == CHAIN_ID for f in items)


async def test_list_facts_for_pair_filters_unfinalized(
    pg_engine: AsyncEngine, clean_facts: _CleanFn
) -> None:
    await clean_facts()
    pending = _swap_fact("0x" + "cc" * 32, T0)
    pending = BlockchainFact(
        **{
            **pending.model_dump(),
            "confirmation_status": ConfirmationStatus.PENDING,
            "confirmations": 0,
        }
    )
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await fact_repos.save_fact(session, _swap_fact("0x" + "bb" * 32, T0))
        await fact_repos.save_fact(session, pending)

    # Default include_unfinalized=False → only FINALIZED.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        items, _ = await fact_repos.list_facts_for_pair(session, CHAIN_ID, POOL, limit=10)
    assert len(items) == 1
    assert items[0].confirmation_status == ConfirmationStatus.FINALIZED

    # include_unfinalized=True → both PENDING and FINALIZED.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        items_all, _ = await fact_repos.list_facts_for_pair(
            session, CHAIN_ID, POOL, include_unfinalized=True, limit=10
        )
    assert len(items_all) == 2


# ---------------------------------------------------------------------------
# list_facts_for_wallet — GIN involved_wallets
# ---------------------------------------------------------------------------


async def test_list_facts_for_wallet_gin(pg_engine: AsyncEngine, clean_facts: _CleanFn) -> None:
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        # SWAP has sender=SENDER → involved_wallets generated column includes it.
        await fact_repos.save_fact(session, _swap_fact("0x" + "bb" * 32, T0))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        items, _ = await fact_repos.list_facts_for_wallet(session, CHAIN_ID, SENDER, limit=10)
    assert len(items) == 1
    assert items[0].fact_type == FactType.SWAP_EXECUTED


# ---------------------------------------------------------------------------
# Paged outcomes / insights
# ---------------------------------------------------------------------------


async def test_list_outcomes_page_and_insights_page(
    pg_engine: AsyncEngine, clean_outcomes: _CleanFn
) -> None:
    await clean_outcomes()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await save_outcome(
            session,
            Outcome.create(
                entity_id=ENTITY_ID,
                outcome_type=OutcomeType.RUG_PULL,
                observation_window="1h",
                label_definition="x",
                label_definition_version="1.0",
                evaluation_timestamp=T0,
                evaluated_at=T0,
                label_value=True,
            ),
        )
        await save_insight(
            session,
            Insight(
                insight_id=f"{ENTITY_ID}|HoneypotDetected|{T0.isoformat()}",
                entity_id=ENTITY_ID,
                insight_type="HoneypotDetected",
                summary="x",
                generated_at=T0,
                source_features=[],
                importance=Importance.HIGH,
            ),
        )

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        outcomes, _ = await list_outcomes_page(session, ENTITY_ID, limit=10)
        insights, _ = await list_insights_page(
            session, ENTITY_ID, limit=10, insight_type="HoneypotDetected"
        )
    assert len(outcomes) == 1
    assert outcomes[0].outcome_type == OutcomeType.RUG_PULL
    assert len(insights) == 1
    assert insights[0].insight_type == "HoneypotDetected"


# ---------------------------------------------------------------------------
# Timescale paged readers + features
# ---------------------------------------------------------------------------


async def test_list_bars_page_and_snapshots_page_keyset(
    pg_engine: AsyncEngine, clean_entities: _CleanFn
) -> None:
    await clean_entities()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_bar(
            session,
            MarketBar.create(
                pair_id=ENTITY_ID,
                chain_id=CHAIN_ID,
                interval=BarInterval.ONE_HOUR,
                bar_start_time=T0,
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
                computed_at=PINNED,
            ),
        )
        await ts_repos.save_snapshot(
            session,
            ObservationSnapshot.create(
                entity_id=ENTITY_ID,
                chain_id=CHAIN_ID,
                snapshot_timestamp=T0,
                observed_at=T0,
                ingested_at=T0,
                source="test",
                reserve0="100",
                reserve1="100",
                price="1",
            ),
        )

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        bars, _ = await ts_repos.list_bars_page(session, ENTITY_ID, BarInterval.ONE_HOUR, limit=10)
        snaps, _ = await ts_repos.list_snapshots_page(session, ENTITY_ID, limit=10)
    assert len(bars) == 1
    assert bars[0].interval == BarInterval.ONE_HOUR
    assert len(snaps) == 1
    assert snaps[0].reserve0 == "100"


async def test_list_features_range_and_latest_features(
    pg_engine: AsyncEngine, clean_entities: _CleanFn
) -> None:
    from onchain_platform.domain.schemas.feature import Feature

    await clean_entities()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_feature(
            session,
            Feature(
                feature_id=f"liquidity_growth_pct_1h|{ENTITY_ID}|{T0.isoformat()}",
                feature_name="liquidity_growth_pct_1h",
                entity_id=ENTITY_ID,
                entity_type="TRADING_PAIR",
                as_of_timestamp=T0,
                computed_at=T0,
                value=10.0,
                inputs=["s1"],
            ),
        )
        await ts_repos.save_feature(
            session,
            Feature(
                feature_id=f"price_momentum_zscore_1h|{ENTITY_ID}|{T0.isoformat()}",
                feature_name="price_momentum_zscore_1h",
                entity_id=ENTITY_ID,
                entity_type="TRADING_PAIR",
                as_of_timestamp=T0,
                computed_at=T0,
                value=1.5,
                inputs=["s1"],
            ),
        )

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        names = await ts_repos.list_feature_names(session, ENTITY_ID)
        ranged = await ts_repos.list_features_range(session, ENTITY_ID, start=T0, end=PINNED)
        latest = await ts_repos.list_latest_features(session, ENTITY_ID, T0)
    assert sorted(names) == ["liquidity_growth_pct_1h", "price_momentum_zscore_1h"]
    assert len(ranged) == 2
    assert len(latest) == 2
    assert {f.feature_name for f in latest} == {
        "liquidity_growth_pct_1h",
        "price_momentum_zscore_1h",
    }
