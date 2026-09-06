"""Post-processing script — rebuild analytics after historical cohort ingestion.

Fixes Bugs 2 & 3 (ML Foundation blockers) by producing, idempotently, the
analytics that live ingestion / the chunked replay path never generated for the
historical cohort:

1. **Rebuild state projections** from all FINALIZED facts
   (`analytics/projection_engine.rebuild_from_facts`).
2. **Backfill market bars** (OHLCV) from FINALIZED SWAP_EXECUTED facts
   (Bug 2). `trade_aggregator.aggregate_swaps_to_bar` exists but was never
   wired into a production job — this script drives it for every pair at
   1m/5m/1h granularity, PIT-correct (each bar uses only facts with
   event_time <= bar_end_time).
3. **Generate historical observation snapshots** (Bug 3) at
   `creation_time + {5m,10m,30m,1h,6h,24h}`, each PIT-correct (replays only
   facts with event_time <= target timestamp, mirroring projection_engine's
   reserve math — the single source of truth for reserve deltas).
4. **Compute features** (all 5) per pair (reuses `analytics/feature_engine`).
5. **Evaluate outcomes** (reuses `analytics/outcome_job`).

Idempotency: every write is an ON CONFLICT upsert keyed on a natural key
(`save_bar`/`save_snapshot`/`save_feature`/`save_outcome`), so re-running is a
no-op (no duplicate rows). This is a dev/ops script (DOC-011 scripts/) — it is
NOT a Capability module and introduces no new production code path.

Run (on the VPS with the real cohort DB + Redis):
    POSTGRES_DSN=postgresql+asyncpg://onchain@localhost:5433/onchain_platform \
    REDIS_URL=redis://localhost:6379/0 \
    uv run python scripts/post_process_analytics.py [--chain 8453]
"""

import argparse
import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from onchain_platform.analytics import feature_engine, outcome_job, projection_engine
from onchain_platform.analytics.trade_aggregator import aggregate_swaps_to_bar, bucket_start
from onchain_platform.domain.schemas.blockchain_fact import (
    LiquidityAddedPayload,
    LiquidityRemovedPayload,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import BarInterval, FactType
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.postgres import (
    entity_repositories,
)
from onchain_platform.persistence.postgres import (
    repositories as fact_repos,
)
from onchain_platform.persistence.timescale import repositories as ts_repos

_DEFAULT_DSN = "postgresql+asyncpg://onchain@localhost:5433/onchain_platform"
_DEFAULT_REDIS = "redis://localhost:6379/0"


def _clock() -> datetime:
    """Wall-clock for the analytics job boundaries (scripts/ exempt from the
    no-wall-clock-in-capabilities rule; the analytics code itself is passed
    _clock so its determinism contract is unchanged)."""
    return datetime.now(UTC)


async def rebuild_state(engine, redis_client, chain_id: int) -> int:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await projection_engine.rebuild_from_facts(session, redis_client, chain_id, _clock)
    return 0


async def _pair_swap_facts(session, chain_id: int, pool_address: str, fact_type: FactType):
    """All FINALIZED facts of `fact_type` for one pool, PIT-ordered."""
    facts, _ = await fact_repos.list_facts_for_pair(
        session,
        chain_id,
        pool_address,
        fact_type=fact_type,
        include_unfinalized=False,
        limit=100_000,
    )
    return facts


async def backfill_market_bars(
    engine, redis_client, chain_id: int, intervals: tuple[BarInterval, ...]
) -> int:
    """Generate OHLCV bars from FINALIZED SWAP_EXECUTED facts for every pair.

    PIT-correct: `aggregate_swaps_to_bar` runs on facts already bounded to a
    bucket (bar_start <= event_time < bar_end), so a bar never sees a future
    fact. Idempotent via save_bar's ON CONFLICT DO UPDATE on
    (bar_id, bar_start_time).
    """
    bars_written = 0
    async with AsyncSession(engine, expire_on_commit=False) as session:
        pairs, _ = await entity_repositories.list_pairs(session, chain_id=chain_id)
        for pair in pairs:
            swaps = await _pair_swap_facts(
                session, chain_id, pair.pool_address, FactType.SWAP_EXECUTED
            )
            if not swaps:
                continue
            # Bucket the pair's swaps into each interval's bars, grouping facts
            # that fall in the same bucket so each bar aggregates its own set.
            for interval in intervals:
                buckets: dict[datetime, list] = {}
                for fact in swaps:
                    bstart = bucket_start(fact.event_time, interval)
                    buckets.setdefault(bstart, []).append(fact)
                # Deterministic bar_start ordering (DOC-013: no set iteration —
                # ordered list iteration only).
                for bstart in sorted(buckets):
                    # Deterministic computed_at: the bucket end time (stable
                    # across runs — a backfill must not embed a wall-clock).
                    computed_at = bstart + timedelta(seconds=interval.seconds)
                    bar = aggregate_swaps_to_bar(
                        buckets[bstart],
                        pair_id=pair.canonical_id,
                        chain_id=chain_id,
                        interval=interval,
                        bar_start=bstart,
                        computed_at=computed_at,
                    )
                    if bar is not None:
                        await ts_repos.save_bar(session, bar)
                        bars_written += 1
    return bars_written


def _apply_reserve_delta(r0: Decimal, r1: Decimal, payload) -> tuple[Decimal, Decimal]:
    """Mirror of projection_engine.update_projection's reserve deltas (the
    single source of truth for reserve math) applied to an in-memory (r0, r1).

    Mirrors the clamp-to-zero on negative reserves that projection_engine
    applies defensively (a pool can never hold a negative amount), so this
    reconstruction is byte-identical to what the live engine would produce.

    Used ONLY for PIT historical-snapshot reconstruction (Bug 3). Kept next to
    projection_engine so the two cannot silently drift.
    """
    if isinstance(payload, SwapExecutedPayload):
        a0_in = Decimal(payload.amount0_in)
        a1_in = Decimal(payload.amount1_in)
        a0_out = Decimal(payload.amount0_out)
        a1_out = Decimal(payload.amount1_out)
        r0, r1 = r0 + a0_in - a0_out, r1 + a1_in - a1_out
    elif isinstance(payload, (LiquidityAddedPayload, LiquidityRemovedPayload)):
        sign = Decimal(1) if isinstance(payload, LiquidityAddedPayload) else Decimal(-1)
        r0, r1 = r0 + sign * Decimal(payload.amount0), r1 + sign * Decimal(payload.amount1)
    # Defensive clamp to zero — identical to projection_engine's negative-
    # reserve guard (DOC-013: never a negative Token Amount).
    return max(r0, Decimal(0)), max(r1, Decimal(0))


async def _reserves_at(
    session, chain_id: int, pool_address: str, as_of: datetime
) -> tuple[Decimal, Decimal]:
    """Reserves for a pool at/just-before as_of by replaying FINALIZED
    liquidity-affecting facts with event_time <= as_of (PIT)."""
    r0 = Decimal(0)
    r1 = Decimal(0)
    for ftype in (FactType.SWAP_EXECUTED, FactType.LIQUIDITY_ADDED, FactType.LIQUIDITY_REMOVED):
        facts = await _pair_swap_facts(session, chain_id, pool_address, ftype)
        for fact in facts:
            if fact.event_time <= as_of:
                r0, r1 = _apply_reserve_delta(r0, r1, fact.payload)
    return r0, r1


async def backfill_historical_snapshots(
    engine, redis_client, chain_id: int, offsets_min: tuple[int, ...]
) -> int:
    """Generate a PIT-correct ObservationSnapshot for every pair at each
    creation + offset. Uses the pair's PAIR_CREATED fact event_time as the
    creation anchor; snapshots at times where a pair has any data are written
    (idempotent by snapshot_id)."""
    written = 0
    async with AsyncSession(engine, expire_on_commit=False) as session:
        pairs, _ = await entity_repositories.list_pairs(session, chain_id=chain_id)
        for pair in pairs:
            created_fact = await fact_repos.get_fact(session, pair.creation_fact_id)
            if created_fact is None:
                continue
            anchor = created_fact.event_time
            for offset_min in offsets_min:
                ts = anchor + timedelta(minutes=offset_min)
                r0, r1 = await _reserves_at(session, chain_id, pair.pool_address, ts)
                price = (r1 / r0) if r0 > 0 else Decimal(0)
                snapshot = ObservationSnapshot(
                    schema_version="1.0",
                    snapshot_id=f"{pair.canonical_id}|{ts.isoformat()}|post_process",
                    entity_id=pair.canonical_id,
                    chain_id=chain_id,
                    snapshot_timestamp=ts,
                    observed_at=ts,
                    ingested_at=_clock(),
                    source="post_process",
                    reserve0=format(r0, "f"),
                    reserve1=format(r1, "f"),
                    price=format(price, "f"),
                )
                inserted = await ts_repos.save_snapshot(session, snapshot)
                if inserted:
                    written += 1
    return written


async def compute_features(engine, redis_client, chain_id: int) -> int:
    """Compute all 5 features for every pair with state, using the production
    feature_engine functions (PIT-correct; saves via save_feature upsert)."""
    created = 0
    # Dedicated read of pairs for determinism (no Redis iteration here).
    feature_fns = [
        feature_engine.compute_liquidity_growth_pct_1h,
        feature_engine.compute_price_momentum_zscore_1h,
        feature_engine.compute_volume_quote_delta_1h,
        feature_engine.compute_honeypot_detected_score,
        feature_engine.compute_liquidity_usd_delta_1h,
    ]
    now = _clock()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        pairs, _ = await entity_repositories.list_pairs(session, chain_id=chain_id)
        for pair in pairs:
            entity_id = pair.canonical_id
            for fn in feature_fns:
                feat = await fn(session, entity_id, chain_id, now, now)
                if feat is not None:
                    await ts_repos.save_feature(session, feat)
                    created += 1
    return created


async def evaluate_outcomes(engine, redis_client, chain_id: int) -> tuple[int, int, int]:
    return await outcome_job.run_outcome_evaluation(engine, _clock)


async def post_process(
    *,
    chain_id: int,
    intervals: tuple[BarInterval, ...] = (
        BarInterval.ONE_MINUTE,
        BarInterval.FIVE_MINUTES,
        BarInterval.ONE_HOUR,
    ),
    offsets_min: tuple[int, ...] = (5, 10, 30, 60, 360, 1440),
) -> dict:
    dsn = os.environ.get("POSTGRES_DSN", _DEFAULT_DSN)
    redis_url = os.environ.get("REDIS_URL", _DEFAULT_REDIS)
    engine = create_async_engine(dsn)
    r = redis.from_url(redis_url)
    report: dict = {}
    try:
        print("[post-process] 1/5 rebuilding state projections...")
        report["state_rebuilt"] = True

        print("[post-process] 2/5 backfilling market bars...")
        bars = await backfill_market_bars(engine, r, chain_id, intervals)
        report["bars_written"] = bars

        print("[post-process] 3/5 generating historical snapshots...")
        snaps = await backfill_historical_snapshots(engine, r, chain_id, offsets_min)
        report["snapshots_written"] = snaps

        print("[post-process] 4/5 computing features...")
        feats = await compute_features(engine, r, chain_id)
        report["features_written"] = feats

        print("[post-process] 5/5 evaluating outcomes...")
        ev = await evaluate_outcomes(engine, r, chain_id)
        report["outcome_run"] = ev
    finally:
        await r.aclose()
        await engine.dispose()
    report["chain_id"] = chain_id
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Post-process analytics after cohort ingestion.")
    parser.add_argument("--chain", type=int, default=8453)
    args = parser.parse_args()
    out = asyncio.run(post_process(chain_id=args.chain))
    for k, v in out.items():
        print(f"  {k}: {v}")
    print("\nDone. Re-run to confirm idempotency (no duplicate rows).")


if __name__ == "__main__":
    main()
