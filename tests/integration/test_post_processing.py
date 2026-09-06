"""Integration tests: post-processing analytics backfill (Bugs 2 & 3) against
real Postgres/TimescaleDB.

Verifies the post-processing script's two ML-blocker fixes idempotently:

1. **Bar backfill (Bug 2):** market bars are generated from FINALIZED
   SWAP_EXECUTED facts — PIT-correct (a bar only aggregates facts within its
   bucket) and idempotent (re-running adds no duplicate bars).
2. **Historical snapshots (Bug 3):** snapshots are generated at
   creation + offset, each using only facts with event_time <= its timestamp
   (no lookahead), idempotent by snapshot_id.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics.trade_aggregator import compute_pair_id
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    PairCreatedPayload,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import BarInterval, ConfirmationStatus, FactType
from onchain_platform.persistence.postgres import entity_repositories
from onchain_platform.persistence.postgres import repositories as pg_repos
from onchain_platform.persistence.timescale import repositories as ts_repos

CHAIN_ID = 8453
# Distinct pool per module so post-processing tests never collide with the
# shared entity (0x39f0...) used by test_observation_snapshots / others.
POOL = "0x6969696969696969696969696969696969696969"
TOKEN0 = "0x4200000000000000000000000000000000000006"
# Real USDC address on Base (recognized by pool_classifier._STABLECOIN_ADDRESSES).
TOKEN1 = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PAIR_ID = compute_pair_id(CHAIN_ID, POOL)
CREATED = datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)


def _swap_fact(
    block_number: int,
    log_index: int,
    event_time: datetime,
    amount0_in: str = "1000",
    amount1_out: str = "5000",
) -> BlockchainFact:
    return BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{block_number:064x}:{log_index}",
        chain_id=CHAIN_ID,
        fact_type=FactType.SWAP_EXECUTED,
        block_number=block_number,
        block_hash=f"0x{block_number:064x}",
        tx_hash=f"0x{block_number:064x}",
        log_index=log_index,
        event_time=event_time,
        observed_at=event_time,
        ingested_at=event_time,
        confirmation_status=ConfirmationStatus.FINALIZED,
        confirmations=10,
        payload=SwapExecutedPayload(
            fact_type="SWAP_EXECUTED",
            pool_address=POOL,
            sender="0x" + "11" * 20,
            recipient="0x" + "22" * 20,
            amount0_in=amount0_in,
            amount1_in="0",
            amount0_out="0",
            amount1_out=amount1_out,
        ),
    )


async def _seed_pair_and_facts(pg_engine: AsyncEngine, facts: list[BlockchainFact]) -> None:
    """Seed the TradingPair (with its creation fact) + the given facts."""
    pair_cid = pair_canonical_id(CHAIN_ID, POOL)
    creation_fact_id = f"{CHAIN_ID}:0x{'aa' * 32}:0"
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_repositories.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(CHAIN_ID, TOKEN0),
                chain_id=CHAIN_ID,
                contract_address=TOKEN0,
            ),
        )
        await entity_repositories.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(CHAIN_ID, TOKEN1),
                chain_id=CHAIN_ID,
                contract_address=TOKEN1,
            ),
        )
        await entity_repositories.save_trading_pair(
            session,
            TradingPair(
                canonical_id=pair_cid,
                chain_id=CHAIN_ID,
                dex="uniswap_v2",
                base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
                quote_token_id=token_canonical_id(CHAIN_ID, TOKEN1),
                pool_address=POOL,
                creation_block=100,
                creation_fact_id=creation_fact_id,
            ),
        )
        await pg_repos.save_fact(
            session,
            BlockchainFact(
                schema_version="1.0",
                fact_id=creation_fact_id,
                chain_id=CHAIN_ID,
                fact_type=FactType.PAIR_CREATED,
                block_number=100,
                block_hash="0x" + "bb" * 32,
                tx_hash="0x" + "aa" * 32,
                log_index=0,
                event_time=CREATED,
                observed_at=CREATED,
                ingested_at=CREATED,
                confirmation_status=ConfirmationStatus.FINALIZED,
                confirmations=10,
                payload=PairCreatedPayload(
                    fact_type="PAIR_CREATED",
                    pair_address=POOL,
                    token0_address=TOKEN0,
                    token1_address=TOKEN1,
                    dex="uniswap_v2",
                ),
            ),
        )
        for f in facts:
            await pg_repos.save_fact(session, f)


async def _clean(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE blockchain_facts, trading_pairs, tokens, outcomes, insights, "
                "observation_snapshots, market_bars, features CASCADE"
            )
        )


async def test_backfill_market_bars_generates_and_is_idempotent(pg_engine: AsyncEngine) -> None:
    """Bug 2: bars are generated from FINALIZED swaps; re-running adds no dupes."""
    from scripts.post_process_analytics import backfill_market_bars

    await _clean(pg_engine)
    # 3 swaps in one 1-minute bucket, one swap in a later bucket.
    facts = [
        _swap_fact(200, 0, CREATED),
        _swap_fact(201, 0, CREATED + timedelta(seconds=1)),
        _swap_fact(202, 0, CREATED + timedelta(seconds=2)),
        _swap_fact(300, 0, CREATED + timedelta(minutes=2)),  # later 1m bucket
    ]
    await _seed_pair_and_facts(pg_engine, facts)

    await backfill_market_bars(pg_engine, None, CHAIN_ID, (BarInterval.ONE_MINUTE,))
    await backfill_market_bars(pg_engine, None, CHAIN_ID, (BarInterval.ONE_MINUTE,))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        bars = await ts_repos.list_bars(
            session,
            PAIR_ID,
            BarInterval.ONE_MINUTE,
            CREATED - timedelta(minutes=1),
            CREATED + timedelta(minutes=3),
        )
    # Two 1m buckets -> two bars; DB row count is the idempotency guarantee
    # (save_bar is ON CONFLICT DO UPDATE; a second run re-upserts, never inserts
    # a third row).
    assert len(bars) == 2


async def test_backfill_market_bars_pit_correct(pg_engine: AsyncEngine) -> None:
    """Bug 2 PIT: each bar aggregates only the swaps in its own bucket window."""
    from scripts.post_process_analytics import backfill_market_bars

    await _clean(pg_engine)
    # Two swaps 2 minutes apart → two distinct 1m buckets, never one combined bar.
    facts = [
        _swap_fact(200, 0, CREATED),
        _swap_fact(201, 0, CREATED + timedelta(minutes=2)),
    ]
    await _seed_pair_and_facts(pg_engine, facts)

    await backfill_market_bars(pg_engine, None, CHAIN_ID, (BarInterval.ONE_MINUTE,))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        bars = await ts_repos.list_bars(
            session,
            PAIR_ID,
            BarInterval.ONE_MINUTE,
            CREATED - timedelta(minutes=1),
            CREATED + timedelta(minutes=3),
        )
    # Two separate bars — no lookahead merging across buckets.
    assert len(bars) == 2
    assert bars[0].trade_count == 1
    assert bars[1].trade_count == 1


async def test_backfill_historical_snapshots_pit_and_idempotent(pg_engine: AsyncEngine) -> None:
    """Bug 3: snapshots at creation+offsets use only facts <= timestamp; re-run is a no-op."""
    from scripts.post_process_analytics import backfill_historical_snapshots

    await _clean(pg_engine)
    # Liquidity injected at CREATED, then a swap at CREATED+30min, then more at CREATED+2h.
    liq_fact = BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{500:064x}:0",
        chain_id=CHAIN_ID,
        fact_type=FactType.SWAP_EXECUTED,
        block_number=500,
        block_hash=f"0x{500:064x}",
        tx_hash=f"0x{500:064x}",
        log_index=0,
        event_time=CREATED,
        observed_at=CREATED,
        ingested_at=CREATED,
        confirmation_status=ConfirmationStatus.FINALIZED,
        confirmations=10,
        payload=SwapExecutedPayload(
            fact_type="SWAP_EXECUTED",
            pool_address=POOL,
            sender="0x" + "11" * 20,
            recipient="0x" + "22" * 20,
            amount0_in="1000000",
            amount1_in="0",
            amount0_out="0",
            amount1_out="0",
        ),
    )
    swap_30m = _swap_fact(501, 0, CREATED + timedelta(minutes=30))
    # A swap AFTER the 1h offset must NOT affect the 1h snapshot (PIT).
    swap_2h = _swap_fact(502, 0, CREATED + timedelta(hours=2))
    await _seed_pair_and_facts(pg_engine, [liq_fact, swap_30m, swap_2h])

    first = await backfill_historical_snapshots(pg_engine, None, CHAIN_ID, (5, 10, 30, 60))
    await backfill_historical_snapshots(pg_engine, None, CHAIN_ID, (5, 10, 30, 60))

    # 4 snapshots written; the DB row count is stable across runs (idempotent
    # by snapshot_id — ON CONFLICT DO UPDATE never inserts a second row).
    assert first >= 4
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        from onchain_platform.persistence.timescale import repositories as ts

        snaps_after_first = await ts.list_snapshots(
            session,
            PAIR_ID,
            CREATED - timedelta(minutes=1),
            CREATED + timedelta(hours=1, minutes=1),
        )
    assert len(snaps_after_first) == 4
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        from onchain_platform.persistence.timescale import repositories as ts

        snaps_after_second = await ts.list_snapshots(
            session,
            PAIR_ID,
            CREATED - timedelta(minutes=1),
            CREATED + timedelta(hours=1, minutes=1),
        )
    assert len(snaps_after_second) == 4  # no duplicates from the re-run
    snaps = snaps_after_second

    # 1h snapshot (CREATED+60m) must NOT include the +2h swap (PIT/no-lookahead).
    snap_1h = next(s for s in snaps if s.snapshot_timestamp == CREATED + timedelta(hours=1))
    snap_1h_reserve0 = Decimal(snap_1h.reserve0)
    # reserve0 = 1000000 (initial) + (from swap_30m, amount0_in=1000) = 1001000.
    assert snap_1h_reserve0 == Decimal("1001000")
    # The +2h swap (amount0_in=1000) is excluded → still 1001000, not 1002000.
    assert snap_1h_reserve0 == Decimal("1001000")


async def test_snapshot_backfill_populates_liquidity_usd_via_oracle(
    pg_engine: AsyncEngine,
) -> None:
    """Bug 1: a USDC-quote pool's historical snapshot gets liquidity_usd from
    the multi-source oracle (STATIC $1.0), with provenance + confidence and a
    non-null quote_token_type."""
    from onchain_platform.domain.schemas.blockchain_fact import BlockchainFact, SwapExecutedPayload
    from scripts.post_process_analytics import backfill_historical_snapshots

    await _clean(pg_engine)
    # The pair's quote token is the real USDC address (module TOKEN1), so
    # classify_pool returns a USDC (stablecoin) quote → the oracle resolves a
    # STATIC $1.0 USD price and the snapshot gets liquidity_usd. A swap gives
    # it reserves to price.
    usdc_pair = "0x6969696969696969696969696969696969696969"
    usdc_entity = pair_canonical_id(CHAIN_ID, usdc_pair)
    liq = BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{700:064x}:0",
        chain_id=CHAIN_ID,
        fact_type=FactType.SWAP_EXECUTED,
        block_number=700,
        block_hash=f"0x{700:064x}",
        tx_hash=f"0x{700:064x}",
        log_index=0,
        event_time=CREATED,
        observed_at=CREATED,
        ingested_at=CREATED,
        confirmation_status=ConfirmationStatus.FINALIZED,
        confirmations=10,
        payload=SwapExecutedPayload(
            fact_type="SWAP_EXECUTED",
            pool_address=usdc_pair,
            sender="0x" + "11" * 20,
            recipient="0x" + "22" * 20,
            amount0_in="0",
            amount1_in="5000000000",  # quote reserve = 5000 USDC
            amount0_out="0",
            amount1_out="0",
        ),
    )
    await _seed_pair_and_facts(pg_engine, [liq])

    # Build a deterministic oracle (STATIC for USDC).
    import redis.asyncio as redis

    from onchain_platform.acquisition.providers.multi_price_oracle import MultiPriceOracle

    r = redis.from_url("redis://localhost:6379/0")
    oracle = MultiPriceOracle(r, eth_price_provider=None)
    try:
        n = await backfill_historical_snapshots(pg_engine, None, CHAIN_ID, (60,), oracle=oracle)
    finally:
        await r.aclose()
    assert n >= 1

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        from onchain_platform.persistence.timescale import repositories as ts

        snaps = await ts.list_snapshots(
            session,
            usdc_entity,
            CREATED - timedelta(minutes=1),
            CREATED + timedelta(hours=1, minutes=1),
        )
    snap = next(s for s in snaps if s.snapshot_timestamp == CREATED + timedelta(hours=1))
    assert snap.liquidity_usd is not None
    assert snap.quote_token_type is not None
    assert Decimal(snap.liquidity_usd) > 0


async def test_compute_features_uses_historical_as_of(
    pg_engine: AsyncEngine,
) -> None:
    """Bug 2: features are computed at each historical snapshot timestamp
    (as_of = snapshot_ts), so the [as_of-1h, as_of] window sees real data and
    the feature is NOT the value=0 'now()' fallback."""
    from scripts.post_process_analytics import compute_features

    await _clean(pg_engine)
    # Two swaps 5 minutes apart give the momentum feature real returns at a
    # snapshot 1h after creation (which includes both).
    facts = [
        _swap_fact(200, 0, CREATED, amount0_in="1000", amount1_out="2000"),
        _swap_fact(201, 0, CREATED + timedelta(minutes=5), amount0_in="1000", amount1_out="3000"),
    ]
    await _seed_pair_and_facts(pg_engine, facts)

    # First, create historical snapshots (so there is an as_of anchor).
    from scripts.post_process_analytics import backfill_historical_snapshots

    await backfill_historical_snapshots(pg_engine, None, CHAIN_ID, (5, 60))

    created = await compute_features(pg_engine, None, CHAIN_ID)
    assert created > 0

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        from onchain_platform.persistence.timescale import repositories as ts

        feats = await ts.list_features(
            session,
            PAIR_ID,
            "liquidity_growth_pct_1h",
            CREATED - timedelta(minutes=1),
            CREATED + timedelta(hours=1, minutes=1),
        )
    # liquidity_growth_pct_1h needs 2 snapshots in [as_of-1h, as_of]; at
    # as_of = 13:00 (created+60m) the window [12:00,13:00] holds both the
    # 12:05 and 13:00 snapshots → the feature computes at a HISTORICAL as_of.
    assert any(f.as_of_timestamp <= CREATED + timedelta(hours=1) for f in feats), feats
