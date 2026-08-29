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

from datetime import datetime, timedelta
from decimal import Decimal

import polars as pl
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.enums import BarInterval, EntityType
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.timescale import repositories as ts_repos

logger = structlog.get_logger(__name__)


def _feature_id(feature_name: str, entity_id: str, as_of: datetime) -> str:
    """Compute feature_id from components (DOC-012 § Composite ID
    Delimiter: '|' not ':')."""
    return f"{feature_name}|{entity_id}|{as_of.isoformat()}"


def assert_not_none(value: str | None) -> str:
    """Narrow a `str | None` to `str`. Callers guarantee non-None (e.g. via a
    prior `is None` check + continue); this makes that invariant audible to
    the type checker without a runtime assert that would raise on valid data."""
    assert value is not None
    return value


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
    from_time = as_of - timedelta(hours=1)
    # list_snapshots uses exclusive upper bound (< to_time). Add epsilon
    # to include snapshots at exactly as_of (PIT semantics: data available
    # at or before as_of).
    upper = as_of + timedelta(seconds=1)
    snapshots = await ts_repos.list_snapshots(session, entity_id, from_time, upper)

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
    from_time = as_of - timedelta(hours=1)
    upper = as_of + timedelta(seconds=1)
    bars = await ts_repos.list_bars(session, entity_id, BarInterval.ONE_MINUTE, from_time, upper)

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


async def compute_volume_quote_delta_1h(
    session: AsyncSession,
    entity_id: str,
    chain_id: int,
    as_of: datetime,
    computed_at: datetime,
) -> Feature | None:
    """Compute quote-token trading volume delta over a 1-hour window.

    delta = (sum volume_quote in last 1h) - (sum volume_quote in prior 1h).
    Signals whether trading activity is accelerating or decaying, in quote
    token units (NOT USD — no USD volume exists on the platform, DOC-011).

    Uses Decimal intermediate math (DOC-008 § Financial Precision); only the
    final Feature.value is float (DOC-012 § Conventions clarification).

    PIT correctness: only uses bars with bar_start_time <= as_of (no lookahead).
    Returns None if no bars exist across the full 2-hour window (insufficient
    data — an absence of bars is not the same as zero volume).
    """
    window_start = as_of - timedelta(hours=1)
    from_time = as_of - timedelta(hours=2)
    # list_bars uses an exclusive upper bound; add epsilon so a bar at exactly
    # as_of is included (PIT: data available at or before as_of).
    upper = as_of + timedelta(seconds=1)
    bars = await ts_repos.list_bars(session, entity_id, BarInterval.ONE_MINUTE, from_time, upper)

    if not bars:
        logger.debug(
            "feature_insufficient_data",
            feature_name="volume_quote_delta_1h",
            entity_id=entity_id,
            bar_count=0,
        )
        return None

    # Deterministic ordering (DOC-013): explicit sort, no reliance on the
    # repository's ordering convention.
    bars.sort(key=lambda b: b.bar_start_time)

    current_sum = Decimal("0")
    previous_sum = Decimal("0")
    for bar in bars:
        if bar.bar_start_time >= window_start:
            current_sum += Decimal(bar.volume_quote)
        else:
            previous_sum += Decimal(bar.volume_quote)

    delta = current_sum - previous_sum

    inputs = [b.bar_id for b in bars]

    return Feature(
        feature_id=_feature_id("volume_quote_delta_1h", entity_id, as_of),
        feature_name="volume_quote_delta_1h",
        entity_id=entity_id,
        entity_type=EntityType.TRADING_PAIR,
        as_of_timestamp=as_of,
        computed_at=computed_at,
        window="1h",
        value=float(delta),
        inputs=inputs,
    )


async def compute_honeypot_detected_score(
    session: AsyncSession,
    entity_id: str,
    chain_id: int,
    as_of: datetime,
    computed_at: datetime,
) -> Feature | None:
    """Compute a binary risk score from the persistence layer's latest
    'HoneypotDetected' Insight as of as_of.

    100.0 if the entity's most recent HoneypotDetected Insight is at or before
    as_of; 0.0 if the entity has been risk-assessed (has any Insight) but none
    of them flagged a honeypot by as_of. Returns None if the entity has no
    Insight at all as of as_of — i.e. it was never scanned, so "not a
    honeypot" cannot be honestly asserted (DOC-008 PIT: an absent assessment
    is not the same as a cleared one).

    The score is derived ONLY from the persisted insights table (read via
    persistence/, never from transient GoPlus RiskSignals — analytics/ may not
    import intelligence/ per DOC-011).
    """
    from onchain_platform.persistence.postgres import outcomes_insights as oi

    honeypot = await oi.get_latest_insight_as_of(session, entity_id, "HoneypotDetected", as_of)
    if honeypot is not None:
        return Feature(
            feature_id=_feature_id("honeypot_detected_score", entity_id, as_of),
            feature_name="honeypot_detected_score",
            entity_id=entity_id,
            entity_type=EntityType.TRADING_PAIR,
            as_of_timestamp=as_of,
            computed_at=computed_at,
            window=None,
            value=100.0,
            inputs=[honeypot.insight_id],
        )

    # No honeypot insight by as_of. Distinguish "assessed & not a honeypot"
    # (score 0) from "never assessed" (None).
    any_insight = await oi.get_latest_insight_as_of(session, entity_id, None, as_of)
    if any_insight is None:
        logger.debug(
            "feature_insufficient_data",
            feature_name="honeypot_detected_score",
            entity_id=entity_id,
            reason="no_risk_assessment",
        )
        return None

    return Feature(
        feature_id=_feature_id("honeypot_detected_score", entity_id, as_of),
        feature_name="honeypot_detected_score",
        entity_id=entity_id,
        entity_type=EntityType.TRADING_PAIR,
        as_of_timestamp=as_of,
        computed_at=computed_at,
        window=None,
        value=0.0,
        inputs=[any_insight.insight_id],
    )


async def compute_liquidity_usd_delta_1h(
    session: AsyncSession,
    entity_id: str,
    chain_id: int,
    as_of: datetime,
    computed_at: datetime,
) -> Feature | None:
    """Compute the USD liquidity change over a 1-hour window.

    delta = (latest liquidity_usd in last 1h) - (latest liquidity_usd in the
    prior 1h). Derived from ObservationSnapshot.liquidity_usd, which the
    platform's domain-aware price oracle populates with confidence tracking
    (DOC-012 § B.3, DOC-014). Exotic pools have liquidity_usd = NULL (DOC-012)
    — such snapshots cannot contribute a USD delta, so the feature returns None
    for a pair with no priced liquidity in either half-window.

    Uses Decimal intermediate math; only Feature.value is float.
    PIT correctness: only uses snapshots with snapshot_timestamp <= as_of.
    """
    window_start = as_of - timedelta(hours=1)
    from_time = as_of - timedelta(hours=2)
    upper = as_of + timedelta(seconds=1)
    snapshots = await ts_repos.list_snapshots(session, entity_id, from_time, upper)

    if not snapshots:
        logger.debug(
            "feature_insufficient_data",
            feature_name="liquidity_usd_delta_1h",
            entity_id=entity_id,
            snapshot_count=0,
        )
        return None

    snapshots.sort(key=lambda s: s.snapshot_timestamp)

    # Latest priced snapshot in each half-window.
    current_snapshot: ObservationSnapshot | None = None
    previous_snapshot: ObservationSnapshot | None = None
    for snap in snapshots:
        if snap.liquidity_usd is None:
            continue
        if snap.snapshot_timestamp >= window_start:
            current_snapshot = snap
        else:
            previous_snapshot = snap

    if current_snapshot is None or previous_snapshot is None:
        logger.debug(
            "feature_insufficient_data",
            feature_name="liquidity_usd_delta_1h",
            entity_id=entity_id,
            reason=(
                f"missing_liquidity_usd current={current_snapshot is not None} "
                f"previous={previous_snapshot is not None}"
            ),
        )
        return None

    current_usd = Decimal(assert_not_none(current_snapshot.liquidity_usd))
    previous_usd = Decimal(assert_not_none(previous_snapshot.liquidity_usd))
    delta = current_usd - previous_usd

    inputs = [current_snapshot.snapshot_id, previous_snapshot.snapshot_id]

    return Feature(
        feature_id=_feature_id("liquidity_usd_delta_1h", entity_id, as_of),
        feature_name="liquidity_usd_delta_1h",
        entity_id=entity_id,
        entity_type=EntityType.TRADING_PAIR,
        as_of_timestamp=as_of,
        computed_at=computed_at,
        window="1h",
        value=float(delta),
        inputs=inputs,
    )
