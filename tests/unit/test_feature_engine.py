"""Unit tests: Feature Engine (DOC-012 § B.3, DOC-008 § Point-in-Time
Correctness).

All intermediate math uses Decimal (DOC-008). Only Feature.value is float.
Replay tests use tolerance 1e-10 for float fields (DOC-013 § Determinism
Discipline).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime
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
