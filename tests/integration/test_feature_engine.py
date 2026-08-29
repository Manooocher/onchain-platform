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
from onchain_platform.domain.schemas.insight import Insight
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


# ---------------------------------------------------------------------------
# New PIT-correct features (Phase 0 Step 1 — ML Foundation): integration.
# Uses a dedicated entity + timestamps so it never depends on ambient data.
# ---------------------------------------------------------------------------

_ML_ENTITY = "eip155:8453/pair:0xfeedface"
_ASOF = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
_PRIOR_90M = _ASOF - timedelta(minutes=90)
_PRIOR_105M = _ASOF - timedelta(minutes=105)
_CURRENT_30M = _ASOF - timedelta(minutes=30)


def _ml_bar(ts: datetime, volume_quote: str, entity_id: str = _ML_ENTITY) -> MarketBar:
    return MarketBar.create(
        pair_id=entity_id,
        chain_id=CHAIN_ID,
        interval=BarInterval.ONE_MINUTE,
        bar_start_time=ts,
        open_="100",
        high="100",
        low="100",
        close="100",
        volume_base="1000",
        volume_quote=volume_quote,
        trade_count=1,
        vwap="100",
        buy_volume="0",
        sell_volume="0",
        source_fact_range=("f1", "f1"),
        computed_at=_ASOF,
    )


def _ml_snapshot(
    ts: datetime, liquidity_usd: str | None, entity_id: str = _ML_ENTITY, reserve0: str = "1000"
) -> ObservationSnapshot:
    snap = ObservationSnapshot.create(
        entity_id=entity_id,
        chain_id=CHAIN_ID,
        snapshot_timestamp=ts,
        observed_at=ts,
        ingested_at=ts,
        source="ml-test",
        reserve0=reserve0,
        reserve1="2000",
        price="2",
    )
    return snap.model_copy(
        update={
            "liquidity_usd": liquidity_usd,
            "liquidity_usd_source": "STATIC" if liquidity_usd is not None else None,
            "liquidity_usd_confidence": 1.0 if liquidity_usd is not None else None,
            "quote_token_type": "USDC" if liquidity_usd is not None else "OTHER",
        }
    )


def _ml_honeypot_insight(generated_at: datetime, entity_id: str = _ML_ENTITY) -> Insight:
    from onchain_platform.domain.schemas.enums import Importance

    return Insight(
        insight_id=f"{entity_id}|HoneypotDetected|{generated_at.isoformat()}",
        entity_id=entity_id,
        insight_type="HoneypotDetected",
        summary="honeypot",
        generated_at=generated_at,
        source_features=[],
        importance=Importance.HIGH,
    )


def _ml_clear_insight(generated_at: datetime, entity_id: str = _ML_ENTITY) -> Insight:
    from onchain_platform.domain.schemas.enums import Importance

    return Insight(
        insight_id=f"{entity_id}|HighRiskDetected|{generated_at.isoformat()}",
        entity_id=entity_id,
        insight_type="HighRiskDetected",
        summary="assessed",
        generated_at=generated_at,
        source_features=[],
        importance=Importance.HIGH,
    )


async def test_volume_quote_delta_1h_from_real_bars(
    pg_engine: AsyncEngine,
) -> None:
    """Insert bars; compute volume delta from real TimescaleDB."""
    from onchain_platform.analytics.feature_engine import compute_volume_quote_delta_1h

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_bar(session, _ml_bar(_PRIOR_105M, "500"))
        await ts_repos.save_bar(session, _ml_bar(_PRIOR_90M, "500"))
        await ts_repos.save_bar(session, _ml_bar(_CURRENT_30M, "1000"))
        await ts_repos.save_bar(session, _ml_bar(_ASOF, "2000"))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await compute_volume_quote_delta_1h(session, _ML_ENTITY, CHAIN_ID, _ASOF, _ASOF)
    assert result is not None
    assert result.feature_name == "volume_quote_delta_1h"
    # prior sum = 1000, current sum = 3000 → +2000.
    assert abs(result.value - 2000.0) < 1e-10
    assert result.window == "1h"
    assert len(result.inputs) == 4


async def test_honeypot_detected_score_from_real_insight(
    pg_engine: AsyncEngine,
) -> None:
    """A persisted HoneypotDetected insight → score 100."""
    from onchain_platform.analytics.feature_engine import compute_honeypot_detected_score
    from onchain_platform.persistence.postgres import outcomes_insights as oi

    # A distinct entity so this test never collides with the clear-insight one.
    entity = "eip155:8453/pair:0xbeefbeef"
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await oi.save_insight(session, _ml_honeypot_insight(_PRIOR_90M, entity))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await compute_honeypot_detected_score(session, entity, CHAIN_ID, _ASOF, _ASOF)
    assert result is not None
    assert result.value == 100.0
    assert len(result.inputs) == 1


async def test_honeypot_detected_score_zero_when_clear_real(
    pg_engine: AsyncEngine,
) -> None:
    """Assessed (non-honeypot insight) → score 0 from real Insight."""
    from onchain_platform.analytics.feature_engine import compute_honeypot_detected_score
    from onchain_platform.persistence.postgres import outcomes_insights as oi

    entity = "eip155:8453/pair:0xcafecafe"
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await oi.save_insight(session, _ml_clear_insight(_PRIOR_90M, entity))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await compute_honeypot_detected_score(session, entity, CHAIN_ID, _ASOF, _ASOF)
    assert result is not None
    assert result.value == 0.0


async def test_liquidity_usd_delta_1h_from_real_snapshots(
    pg_engine: AsyncEngine,
) -> None:
    """Insert priced snapshots; compute USD delta from real TimescaleDB."""
    from onchain_platform.analytics.feature_engine import compute_liquidity_usd_delta_1h

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_snapshot(session, _ml_snapshot(_PRIOR_90M, "10000"))
        await ts_repos.save_snapshot(session, _ml_snapshot(_CURRENT_30M, "4000"))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await compute_liquidity_usd_delta_1h(session, _ML_ENTITY, CHAIN_ID, _ASOF, _ASOF)
    assert result is not None
    assert result.feature_name == "liquidity_usd_delta_1h"
    assert abs(result.value - (-6000.0)) < 1e-10
    assert result.window == "1h"
    assert len(result.inputs) == 2


async def test_liquidity_usd_delta_1h_none_for_unpriced_real(
    pg_engine: AsyncEngine,
) -> None:
    """Exotic (unpriced) snapshots → None."""
    from onchain_platform.analytics.feature_engine import compute_liquidity_usd_delta_1h

    entity = "eip155:8453/pair:0xdeadbeef"
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_snapshot(session, _ml_snapshot(_PRIOR_90M, None, entity))
        await ts_repos.save_snapshot(session, _ml_snapshot(_CURRENT_30M, None, entity))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await compute_liquidity_usd_delta_1h(session, entity, CHAIN_ID, _ASOF, _ASOF)
    assert result is None


async def test_all_five_features_compute_and_pass_name_validation(
    pg_engine: AsyncEngine,
) -> None:
    """All 5 features compute from real data and satisfy DOC-012 naming."""
    from onchain_platform.analytics.feature_engine import (
        compute_honeypot_detected_score,
        compute_liquidity_growth_pct_1h,
        compute_liquidity_usd_delta_1h,
        compute_price_momentum_zscore_1h,
        compute_volume_quote_delta_1h,
    )

    # Seed bars + snapshots + clear insight for a dedicated entity.
    entity = "eip155:8453/pair:0x0dd1"
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        # Bars: 3 recent (within the 1h window for price_momentum) + prior for volume delta.
        await ts_repos.save_bar(session, _ml_bar(_PRIOR_105M, "500", entity))
        await ts_repos.save_bar(session, _ml_bar(_CURRENT_30M, "1000", entity))
        mid = _CURRENT_30M + timedelta(minutes=15)
        await ts_repos.save_bar(session, _ml_bar(mid, "1500", entity))
        await ts_repos.save_bar(session, _ml_bar(_ASOF, "2000", entity))
        # Snapshots: 2 recent within 1h (liquidity_growth + liquidity_usd_delta).
        await ts_repos.save_snapshot(session, _ml_snapshot(_PRIOR_90M, "10000", entity))
        await ts_repos.save_snapshot(session, _ml_snapshot(_CURRENT_30M, "4000", entity))
        await ts_repos.save_snapshot(session, _ml_snapshot(_ASOF, "6000", entity))
        from onchain_platform.persistence.postgres import outcomes_insights as oi

        await oi.save_insight(session, _ml_clear_insight(_PRIOR_90M, entity))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        # Sequential (shared session — never concurrent, DOC-013 § Async Conventions).
        features = [
            await compute_liquidity_growth_pct_1h(session, entity, CHAIN_ID, _ASOF, _ASOF),
            await compute_price_momentum_zscore_1h(session, entity, CHAIN_ID, _ASOF, _ASOF),
            await compute_volume_quote_delta_1h(session, entity, CHAIN_ID, _ASOF, _ASOF),
            await compute_honeypot_detected_score(session, entity, CHAIN_ID, _ASOF, _ASOF),
            await compute_liquidity_usd_delta_1h(session, entity, CHAIN_ID, _ASOF, _ASOF),
        ]

    present = [f for f in features if f is not None]
    names = [f.feature_name for f in present]
    assert len(present) == 5, f"expected all 5 features, got {names}"

    suffixes = ("_pct", "_ratio", "_score", "_zscore", "_usd", "_delta")
    for feature in present:
        assert any(s in feature.feature_name for s in suffixes), feature.feature_name
        assert feature.entity_type == EntityType.TRADING_PAIR


async def test_new_features_pit_do_not_use_future_data(
    pg_engine: AsyncEngine,
) -> None:
    """Future data (after as_of) must be excluded from every new feature."""
    from onchain_platform.analytics.feature_engine import (
        compute_honeypot_detected_score,
        compute_liquidity_usd_delta_1h,
        compute_volume_quote_delta_1h,
    )

    entity = "eip155:8453/pair:0xfa11"
    future = _ASOF + timedelta(hours=1)
    # Priced state available BEFORE as_of.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_bar(session, _ml_bar(_PRIOR_90M, "1000", entity))
        await ts_repos.save_bar(session, _ml_bar(_CURRENT_30M, "3000", entity))
        await ts_repos.save_snapshot(session, _ml_snapshot(_PRIOR_90M, "5000", entity))
        await ts_repos.save_snapshot(session, _ml_snapshot(_CURRENT_30M, "2000", entity))
        from onchain_platform.persistence.postgres import outcomes_insights as oi

        await oi.save_insight(session, _ml_honeypot_insight(_CURRENT_30M, entity))
        # FUTURE data that must be ignored.
        await ts_repos.save_bar(session, _ml_bar(future, "999999", entity))
        await ts_repos.save_snapshot(session, _ml_snapshot(future, "999999", entity))
        await oi.save_insight(session, _ml_clear_insight(future, entity))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        vol = await compute_volume_quote_delta_1h(session, entity, CHAIN_ID, _ASOF, _ASOF)
        honeypot = await compute_honeypot_detected_score(session, entity, CHAIN_ID, _ASOF, _ASOF)
        liq = await compute_liquidity_usd_delta_1h(session, entity, CHAIN_ID, _ASOF, _ASOF)

    # Future bar (999999) excluded → current +3000, prior +1000 → +2000.
    assert vol is not None and abs(vol.value - 2000.0) < 1e-10
    # Future clear-insight excluded → as of as_of the latest honeypot insight wins → 100.
    assert honeypot is not None and honeypot.value == 100.0
    # Future snapshot (999999) excluded → 2000 - 5000 = -3000.
    assert liq is not None and abs(liq.value - (-3000.0)) < 1e-10
