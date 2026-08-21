"""Integration tests: Feature Engine against real Postgres/TimescaleDB.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions). Feature.value is float — tolerance 1e-10 (DOC-013 §
Determinism Discipline). All other fields byte-identical.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics import feature_engine
from onchain_platform.domain.schemas.enums import BarInterval, EntityType
from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.timescale import repositories as ts_repos

CHAIN_ID = 8453
ENTITY_ID = "eip155:8453/pair:0xabc"
PINNED = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
T_1H_AGO = PINNED - timedelta(hours=1)
T_30M_AGO = PINNED - timedelta(minutes=30)


def _make_snapshot(ts: datetime, reserve0: str, reserve1: str) -> ObservationSnapshot:
    return ObservationSnapshot.create(
        entity_id=ENTITY_ID,
        chain_id=CHAIN_ID,
        snapshot_timestamp=ts,
        observed_at=ts,
        ingested_at=ts,
        source="test",
        reserve0=reserve0,
        reserve1=reserve1,
        price=str(Decimal(reserve1) / Decimal(reserve0)) if Decimal(reserve0) > 0 else "0",
    )


def _make_bar(ts: datetime, close: str) -> MarketBar:
    return MarketBar.create(
        pair_id=ENTITY_ID,
        chain_id=CHAIN_ID,
        interval=BarInterval.ONE_MINUTE,
        bar_start_time=ts,
        open_=close,
        high=close,
        low=close,
        close=close,
        volume_base="1000",
        volume_quote="2000",
        trade_count=1,
        vwap=close,
        buy_volume="500",
        sell_volume="500",
        source_fact_range=("f1", "f1"),
        computed_at=PINNED,
    )


async def test_liquidity_growth_from_real_snapshots(
    pg_engine: AsyncEngine,
) -> None:
    """Insert ObservationSnapshots, compute feature, verify value and inputs."""
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_snapshot(session, _make_snapshot(T_1H_AGO, "1000", "2000"))
        await ts_repos.save_snapshot(session, _make_snapshot(PINNED, "1500", "3000"))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await feature_engine.compute_liquidity_growth_pct_1h(
            session, ENTITY_ID, CHAIN_ID, PINNED, PINNED
        )

    assert result is not None
    assert result.feature_name == "liquidity_growth_pct_1h"
    assert result.entity_type == EntityType.TRADING_PAIR
    # Decimal intermediate: (1500 - 1000) / 1000 * 100 = 50.0
    assert abs(result.value - 50.0) < 1e-10
    assert len(result.inputs) == 2
    assert result.window == "1h"


async def test_price_momentum_from_real_bars(
    pg_engine: AsyncEngine,
) -> None:
    """Insert MarketBars, compute z-score feature, verify value."""
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_bar(session, _make_bar(T_1H_AGO, "100"))
        await ts_repos.save_bar(session, _make_bar(T_30M_AGO, "110"))
        await ts_repos.save_bar(session, _make_bar(PINNED, "105"))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await feature_engine.compute_price_momentum_zscore_1h(
            session, ENTITY_ID, CHAIN_ID, PINNED, PINNED
        )

    assert result is not None
    assert result.feature_name == "price_momentum_zscore_1h"
    assert isinstance(result.value, float)
    assert len(result.inputs) == 3


async def test_pit_query_returns_correct_feature(
    pg_engine: AsyncEngine,
) -> None:
    """Insert features at T1, T2, T3; query at T2 → returns T2's feature."""
    from onchain_platform.domain.schemas.feature import Feature

    t1 = datetime(2024, 4, 22, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2024, 4, 22, 11, 0, 0, tzinfo=UTC)
    t3 = datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)

    f1 = Feature(
        feature_id=f"test_pct|{ENTITY_ID}|{t1.isoformat()}",
        feature_name="test_pct",
        entity_id=ENTITY_ID,
        entity_type="TRADING_PAIR",
        as_of_timestamp=t1,
        computed_at=t1,
        value=10.0,
        inputs=["snap1"],
    )
    f2 = Feature(
        feature_id=f"test_pct|{ENTITY_ID}|{t2.isoformat()}",
        feature_name="test_pct",
        entity_id=ENTITY_ID,
        entity_type="TRADING_PAIR",
        as_of_timestamp=t2,
        computed_at=t2,
        value=20.0,
        inputs=["snap2"],
    )
    f3 = Feature(
        feature_id=f"test_pct|{ENTITY_ID}|{t3.isoformat()}",
        feature_name="test_pct",
        entity_id=ENTITY_ID,
        entity_type="TRADING_PAIR",
        as_of_timestamp=t3,
        computed_at=t3,
        value=30.0,
        inputs=["snap3"],
    )

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_feature(session, f1)
        await ts_repos.save_feature(session, f2)
        await ts_repos.save_feature(session, f3)

    # Query at T2 → returns T2's feature (most recent <= T2).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await ts_repos.get_feature_at(session, ENTITY_ID, "test_pct", t2)
    assert result is not None
    assert abs(result.value - 20.0) < 1e-10

    # Query between T2 and T3 → still returns T2.
    t_between = datetime(2024, 4, 22, 11, 30, 0, tzinfo=UTC)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result2 = await ts_repos.get_feature_at(session, ENTITY_ID, "test_pct", t_between)
    assert result2 is not None
    assert abs(result2.value - 20.0) < 1e-10


async def test_feature_upsert_idempotent(
    pg_engine: AsyncEngine,
) -> None:
    """Compute same feature twice → no duplicate, same value."""
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_snapshot(session, _make_snapshot(T_1H_AGO, "1000", "2000"))
        await ts_repos.save_snapshot(session, _make_snapshot(PINNED, "1500", "3000"))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        r1 = await feature_engine.compute_liquidity_growth_pct_1h(
            session, ENTITY_ID, CHAIN_ID, PINNED, PINNED
        )
    assert r1 is not None

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        r2 = await feature_engine.compute_liquidity_growth_pct_1h(
            session, ENTITY_ID, CHAIN_ID, PINNED, PINNED
        )
    assert r2 is not None

    # Both should have the same value (idempotent upsert).
    assert abs(r1.value - r2.value) < 1e-10
    assert r1.feature_id == r2.feature_id
