"""Unit tests: Feature Engine (DOC-012 § B.3, DOC-008 § Point-in-Time
Correctness).

All intermediate math uses Decimal (DOC-008). Only Feature.value is float.
Replay tests use tolerance 1e-10 for float fields (DOC-013 § Determinism
Discipline).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from onchain_platform.analytics.feature_engine import (
    compute_liquidity_growth_pct_1h,
    compute_price_momentum_zscore_1h,
)
from onchain_platform.domain.schemas.enums import BarInterval, EntityType
from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot

CHAIN_ID = 8453
ENTITY_ID = "eip155:8453/pair:0xabc"
PINNED = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
T_1H_AGO = datetime(2026, 8, 20, 11, 0, 0, tzinfo=UTC)
T_30M_AGO = datetime(2026, 8, 20, 11, 30, 0, tzinfo=UTC)


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


@pytest.mark.asyncio
async def test_liquidity_growth_basic() -> None:
    """reserve0: 1000 → 1500 → growth = 50%."""
    session = AsyncMock()
    # Mock list_snapshots to return two snapshots.
    import onchain_platform.persistence.timescale.repositories as ts_repos

    original = ts_repos.list_snapshots
    ts_repos.list_snapshots = AsyncMock(
        return_value=[
            _make_snapshot(T_1H_AGO, "1000", "2000"),
            _make_snapshot(PINNED, "1500", "3000"),
        ]
    )

    try:
        result = await compute_liquidity_growth_pct_1h(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        ts_repos.list_snapshots = original

    assert result is not None
    assert result.feature_name == "liquidity_growth_pct_1h"
    assert result.entity_type == EntityType.TRADING_PAIR
    assert result.window == "1h"
    # Decimal intermediate: (1500 - 1000) / 1000 * 100 = 50.0
    assert abs(result.value - 50.0) < 1e-10
    assert len(result.inputs) == 2


@pytest.mark.asyncio
async def test_liquidity_growth_insufficient_data() -> None:
    """Only 1 snapshot → returns None."""
    session = AsyncMock()
    import onchain_platform.persistence.timescale.repositories as ts_repos

    original = ts_repos.list_snapshots
    ts_repos.list_snapshots = AsyncMock(
        return_value=[
            _make_snapshot(PINNED, "1000", "2000"),
        ]
    )

    try:
        result = await compute_liquidity_growth_pct_1h(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        ts_repos.list_snapshots = original

    assert result is None


@pytest.mark.asyncio
async def test_liquidity_growth_zero_reserve_returns_none() -> None:
    """reserve0 = 0 → division by zero → returns None."""
    session = AsyncMock()
    import onchain_platform.persistence.timescale.repositories as ts_repos

    original = ts_repos.list_snapshots
    ts_repos.list_snapshots = AsyncMock(
        return_value=[
            _make_snapshot(T_1H_AGO, "0", "2000"),
            _make_snapshot(PINNED, "1000", "3000"),
        ]
    )

    try:
        result = await compute_liquidity_growth_pct_1h(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        ts_repos.list_snapshots = original

    assert result is None


@pytest.mark.asyncio
async def test_price_momentum_zscore_basic() -> None:
    """3 bars with known prices → assert z-score within tolerance."""
    session = AsyncMock()
    import onchain_platform.persistence.timescale.repositories as ts_repos

    original = ts_repos.list_bars
    ts_repos.list_bars = AsyncMock(
        return_value=[
            _make_bar(T_1H_AGO, "100"),
            _make_bar(T_30M_AGO, "110"),
            _make_bar(PINNED, "105"),
        ]
    )

    try:
        result = await compute_price_momentum_zscore_1h(
            session, ENTITY_ID, CHAIN_ID, PINNED, PINNED
        )
    finally:
        ts_repos.list_bars = original

    assert result is not None
    assert result.feature_name == "price_momentum_zscore_1h"
    assert result.window == "1h"
    assert isinstance(result.value, float)
    assert len(result.inputs) == 3


@pytest.mark.asyncio
async def test_price_momentum_zscore_insufficient_data() -> None:
    """Only 1 bar → returns None."""
    session = AsyncMock()
    import onchain_platform.persistence.timescale.repositories as ts_repos

    original = ts_repos.list_bars
    ts_repos.list_bars = AsyncMock(
        return_value=[
            _make_bar(PINNED, "100"),
        ]
    )

    try:
        result = await compute_price_momentum_zscore_1h(
            session, ENTITY_ID, CHAIN_ID, PINNED, PINNED
        )
    finally:
        ts_repos.list_bars = original

    assert result is None


@pytest.mark.asyncio
async def test_price_momentum_zscore_zero_std_returns_zero() -> None:
    """All prices identical → std=0 → returns 0.0 (avoids division by zero)."""
    session = AsyncMock()
    import onchain_platform.persistence.timescale.repositories as ts_repos

    original = ts_repos.list_bars
    ts_repos.list_bars = AsyncMock(
        return_value=[
            _make_bar(T_1H_AGO, "100"),
            _make_bar(T_30M_AGO, "100"),
            _make_bar(PINNED, "100"),
        ]
    )

    try:
        result = await compute_price_momentum_zscore_1h(
            session, ENTITY_ID, CHAIN_ID, PINNED, PINNED
        )
    finally:
        ts_repos.list_bars = original

    assert result is not None
    assert result.value == 0.0


# ---------------------------------------------------------------------------
# New PIT-correct features (Phase 0 Step 1 — ML Foundation)
# ---------------------------------------------------------------------------


def _make_bar_with_volume(ts: datetime, volume_quote: str) -> MarketBar:
    # MarketBar is frozen; build via create with the desired volume.
    return MarketBar.create(
        pair_id=ENTITY_ID,
        chain_id=CHAIN_ID,
        interval=BarInterval.ONE_MINUTE,
        bar_start_time=ts,
        open_="100",
        high="100",
        low="100",
        close="100",
        volume_base="X",
        volume_quote=volume_quote,
        trade_count=1,
        vwap="100",
        buy_volume="0",
        sell_volume="0",
        source_fact_range=("f1", "f1"),
        computed_at=PINNED,
    )


def _make_snapshot_with_liq_usd(
    ts: datetime, liquidity_usd: str | None, reserve0: str = "1000", reserve1: str = "2000"
) -> ObservationSnapshot:
    snap = ObservationSnapshot.create(
        entity_id=ENTITY_ID,
        chain_id=CHAIN_ID,
        snapshot_timestamp=ts,
        observed_at=ts,
        ingested_at=ts,
        source="test",
        reserve0=reserve0,
        reserve1=reserve1,
        price="2",
    )
    return snap.model_copy(
        update={
            "liquidity_usd": liquidity_usd,
            "liquidity_usd_source": "STATIC",
            "liquidity_usd_confidence": 1.0,
        }
    )


@pytest.mark.asyncio
async def test_volume_quote_delta_1h_acceleration() -> None:
    """Volume increases: current 1h (3000) > prior 1h (1000) → delta +2000."""
    from onchain_platform.analytics.feature_engine import compute_volume_quote_delta_1h

    session = AsyncMock()
    import onchain_platform.persistence.timescale.repositories as ts_repos

    # as_of = 12:00; window_start = 11:00. Current window [11:00, 12:00],
    # prior window [10:00, 11:00). Bars at >= window_start are current.
    # prior sum = 1000, current sum = 3000 → delta = +2000.
    prior_90m = PINNED - timedelta(minutes=90)
    prior_105m = PINNED - timedelta(minutes=105)
    original = ts_repos.list_bars
    ts_repos.list_bars = AsyncMock(
        return_value=[
            _make_bar_with_volume(prior_105m, "500"),  # prior 1h
            _make_bar_with_volume(prior_90m, "500"),  # prior 1h
            _make_bar_with_volume(T_30M_AGO, "1000"),  # current 1h
            _make_bar_with_volume(PINNED, "2000"),  # current 1h
        ]
    )
    try:
        result = await compute_volume_quote_delta_1h(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        ts_repos.list_bars = original

    assert result is not None
    assert result.feature_name == "volume_quote_delta_1h"
    assert result.window == "1h"
    assert abs(result.value - 2000.0) < 1e-10
    assert len(result.inputs) == 4


@pytest.mark.asyncio
async def test_volume_quote_delta_1h_empty_returns_none() -> None:
    """No bars in the 2-hour window → None (not zero)."""
    from onchain_platform.analytics.feature_engine import compute_volume_quote_delta_1h

    session = AsyncMock()
    import onchain_platform.persistence.timescale.repositories as ts_repos

    original = ts_repos.list_bars
    ts_repos.list_bars = AsyncMock(return_value=[])
    try:
        result = await compute_volume_quote_delta_1h(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        ts_repos.list_bars = original

    assert result is None


@pytest.mark.asyncio
async def test_honeypot_detected_score_is_100_when_insight_exists() -> None:
    from onchain_platform.analytics.feature_engine import compute_honeypot_detected_score
    from onchain_platform.domain.schemas.enums import Importance
    from onchain_platform.domain.schemas.insight import Insight

    session = AsyncMock()
    import onchain_platform.persistence.postgres.outcomes_insights as oi

    original = oi.get_latest_insight_as_of
    oi.get_latest_insight_as_of = AsyncMock(
        side_effect=[
            Insight(
                insight_id=f"{ENTITY_ID}|HoneypotDetected|{PINNED.isoformat()}",
                entity_id=ENTITY_ID,
                insight_type="HoneypotDetected",
                summary="honeypot",
                generated_at=PINNED,
                importance=Importance.HIGH,
            ),
        ]
    )
    try:
        result = await compute_honeypot_detected_score(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        oi.get_latest_insight_as_of = original

    assert result is not None
    assert result.feature_name == "honeypot_detected_score"
    assert result.value == 100.0
    assert len(result.inputs) == 1


@pytest.mark.asyncio
async def test_honeypot_detected_score_zero_when_assessed_but_clear() -> None:
    """Entity assessed (some non-honeypot insight present) → 0.0."""
    from onchain_platform.analytics.feature_engine import compute_honeypot_detected_score
    from onchain_platform.domain.schemas.enums import Importance
    from onchain_platform.domain.schemas.insight import Insight

    session = AsyncMock()
    import onchain_platform.persistence.postgres.outcomes_insights as oi

    clear_insight = Insight(
        insight_id=f"{ENTITY_ID}|HighRiskDetected|{PINNED.isoformat()}",
        entity_id=ENTITY_ID,
        insight_type="HighRiskDetected",
        summary="assessed",
        generated_at=PINNED,
        importance=Importance.HIGH,
    )
    original = oi.get_latest_insight_as_of
    oi.get_latest_insight_as_of = AsyncMock(
        side_effect=[None, clear_insight]  # honeypot: none, any: present
    )
    try:
        result = await compute_honeypot_detected_score(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        oi.get_latest_insight_as_of = original

    assert result is not None
    assert result.value == 0.0
    assert result.inputs == [clear_insight.insight_id]


@pytest.mark.asyncio
async def test_honeypot_detected_score_none_when_never_assessed() -> None:
    """No insight at all → None (not a fabricated 0)."""
    from onchain_platform.analytics.feature_engine import compute_honeypot_detected_score

    session = AsyncMock()
    import onchain_platform.persistence.postgres.outcomes_insights as oi

    original = oi.get_latest_insight_as_of
    oi.get_latest_insight_as_of = AsyncMock(side_effect=[None, None])
    try:
        result = await compute_honeypot_detected_score(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        oi.get_latest_insight_as_of = original

    assert result is None


@pytest.mark.asyncio
async def test_liquidity_usd_delta_1h_decrease() -> None:
    """Liquidity_drop: prior 1h 10000 → current 1h 4000 → delta -6000."""
    from onchain_platform.analytics.feature_engine import compute_liquidity_usd_delta_1h

    session = AsyncMock()
    import onchain_platform.persistence.timescale.repositories as ts_repos

    # Liquidity drop: prior-1h (10:30) = 10000 → current-1h (11:30) = 4000.
    prior_90m = PINNED - timedelta(minutes=90)
    original = ts_repos.list_snapshots
    ts_repos.list_snapshots = AsyncMock(
        return_value=[
            _make_snapshot_with_liq_usd(prior_90m, "10000"),  # prior 1h (10:30)
            _make_snapshot_with_liq_usd(T_30M_AGO, "4000"),  # current 1h (11:30)
        ]
    )
    try:
        result = await compute_liquidity_usd_delta_1h(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        ts_repos.list_snapshots = original

    assert result is not None
    assert result.feature_name == "liquidity_usd_delta_1h"
    assert result.window == "1h"
    assert abs(result.value - (-6000.0)) < 1e-10
    assert len(result.inputs) == 2


@pytest.mark.asyncio
async def test_liquidity_usd_delta_1h_none_when_unpriced() -> None:
    """All snapshots lack liquidity_usd (exotic pool) → None."""
    from onchain_platform.analytics.feature_engine import compute_liquidity_usd_delta_1h

    session = AsyncMock()
    import onchain_platform.persistence.timescale.repositories as ts_repos

    original = ts_repos.list_snapshots
    ts_repos.list_snapshots = AsyncMock(
        return_value=[
            _make_snapshot_with_liq_usd(T_1H_AGO, None),
            _make_snapshot_with_liq_usd(PINNED, None),
        ]
    )
    try:
        result = await compute_liquidity_usd_delta_1h(session, ENTITY_ID, CHAIN_ID, PINNED, PINNED)
    finally:
        ts_repos.list_snapshots = original

    assert result is None
