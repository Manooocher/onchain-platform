"""Feature Engine — computes Features from ObservationSnapshots and
MarketBars (DOC-012 § B.3, DOC-006 § Data Lifecycle).

DOC-012 § B.3: Features are "derived from Facts, Observation Snapshots,
Market Bars, Metadata." They are batch computations, not event-driven.

All intermediate math uses Decimal (DOC-008 § Financial Precision). Only
the final Feature.value output is float (DOC-012 § Conventions
clarification: "any field genuinely computed by the Feature Engine from
one or more Decimal inputs is float in the Feature schema").

Point-in-Time correctness (DOC-008 § D): every computation only uses
information available at as_of_timestamp. Never future data.

Determinism (DOC-013 § Determinism Discipline): no wall-clock reads —
as_of is always a parameter. No set iteration on aggregation paths.
Polars runs multi-threaded (default) — replay tests use tolerance for
float fields, never byte-identical.
"""

from datetime import datetime
from decimal import Decimal

import polars as pl
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.enums import BarInterval, EntityType
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.persistence.timescale import repositories as ts_repos

logger = structlog.get_logger(__name__)


def _feature_id(feature_name: str, entity_id: str, as_of: datetime) -> str:
    """Compute feature_id from components (DOC-012 § Composite ID
    Delimiter: '|' not ':')."""
    return f"{feature_name}|{entity_id}|{as_of.isoformat()}"


async def compute_liquidity_growth_pct_1h(
    session: AsyncSession,
    entity_id: str,
    chain_id: int,
    as_of: datetime,
    computed_at: datetime,
) -> Feature | None:
    """Compute liquidity growth percentage over a 1-hour window.

    Formula: (latest_reserve0 - oldest_reserve0) / oldest_reserve0 * 100
    Uses Decimal for intermediate math (DOC-008 § Financial Precision).
    Returns None if insufficient data (< 2 snapshots in window).

    PIT correctness: only uses snapshots with snapshot_timestamp <= as_of.
    """
    from_time = as_of - __import__("datetime").timedelta(hours=1)
    snapshots = await ts_repos.list_snapshots(session, entity_id, from_time, as_of)

    if len(snapshots) < 2:
        logger.debug(
            "feature_insufficient_data",
            feature_name="liquidity_growth_pct_1h",
            entity_id=entity_id,
            snapshot_count=len(snapshots),
        )
        return None

    # Sort by timestamp (list already sorted by repository, but be explicit
    # for determinism — DOC-013: no reliance on implicit ordering).
    snapshots.sort(key=lambda s: s.snapshot_timestamp)

    oldest = snapshots[0]
    latest = snapshots[-1]

    # Decimal intermediate math (DOC-008 § Financial Precision).
    oldest_reserve = Decimal(oldest.reserve0)
    latest_reserve = Decimal(latest.reserve0)

    if oldest_reserve == 0:
        return None  # Avoid division by zero.

    growth = (latest_reserve - oldest_reserve) / oldest_reserve * Decimal(100)

    # inputs: list of snapshot_ids used (DOC-012 § Traceability Chain).
    inputs = [s.snapshot_id for s in snapshots]

    return Feature(
        feature_id=_feature_id("liquidity_growth_pct_1h", entity_id, as_of),
        feature_name="liquidity_growth_pct_1h",
        entity_id=entity_id,
        entity_type=EntityType.TRADING_PAIR,
        as_of_timestamp=as_of,
        computed_at=computed_at,
        window="1h",
        value=float(growth),  # Only final output is float.
        inputs=inputs,
    )


async def compute_price_momentum_zscore_1h(
    session: AsyncSession,
    entity_id: str,
    chain_id: int,
    as_of: datetime,
    computed_at: datetime,
) -> Feature | None:
    """Compute z-score of price momentum over a 1-hour window.

    Uses Polars for vectorized computation (DOC-010 § Data Processing).
    Returns None if insufficient data (< 2 bars in window).

    PIT correctness: only uses bars with bar_start_time <= as_of.
    """
    from_time = as_of - __import__("datetime").timedelta(hours=1)
    bars = await ts_repos.list_bars(session, entity_id, BarInterval.ONE_MINUTE, from_time, as_of)

    if len(bars) < 2:
        logger.debug(
            "feature_insufficient_data",
            feature_name="price_momentum_zscore_1h",
            entity_id=entity_id,
            bar_count=len(bars),
        )
        return None

    # Build Polars DataFrame from bars (list, not set — DOC-013: no set
    # iteration on aggregation paths).
    prices = [float(Decimal(b.close)) for b in bars]
    df = pl.DataFrame({"close": prices})

    # Compute returns: close[i] - close[i-1].
    returns_series = df["close"].diff().drop_nulls()

    if returns_series.len() < 2:
        return None

    mean_val: float = float(returns_series.mean())  # type: ignore[arg-type]
    std_val: float = float(returns_series.std())  # type: ignore[arg-type]

    if std_val == 0.0:
        # No variation → no momentum (avoids division by zero).
        zscore = 0.0
    else:
        latest_return: float = float(returns_series[-1])
        zscore = (latest_return - mean_val) / std_val

    # inputs: list of bar_ids used (DOC-012 § Traceability Chain).
    inputs = [b.bar_id for b in bars]

    return Feature(
        feature_id=_feature_id("price_momentum_zscore_1h", entity_id, as_of),
        feature_name="price_momentum_zscore_1h",
        entity_id=entity_id,
        entity_type=EntityType.TRADING_PAIR,
        as_of_timestamp=as_of,
        computed_at=computed_at,
        window="1h",
        value=float(zscore),
        inputs=inputs,
    )
