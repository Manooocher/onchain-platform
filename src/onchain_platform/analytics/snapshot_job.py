"""Observation snapshot production job (Milestone 5 gap / TD-3).

The M5 snapshot capability was implemented but never wired into production —
snapshots were only written by tests. This module is the APScheduler job that
creates an ObservationSnapshot for every active pair on an interval, so
historical state queries (`/entities/{id}/snapshots`) and feature engineering
have real data.

Design:
- Reads the active pairs from Postgres (via list_pairs — any pair; a pair is
  only snapshotted once it has live Redis state anyway).
- For each, loads the current StateProjection from Redis (the live
  continuously-recomputed read model, DOC-012 § B.2).
- Emits an ObservationSnapshot with snapshot_timestamp = now (injected clock)
  and source = "projection_engine:poll:60s" (M5 convention).
- Writes via the Timescale repository (idempotent per snapshot_id).

Determinism (DOC-013): no wall-clock inside this module — `clock` is the
injected callable from main.py. Import-linter: analytics/ may import
persistence/ and transport/ (cross-cutting infra).
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import redis.asyncio as redis
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.interfaces.price_oracle import PriceOracle
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.postgres import entity_repositories
from onchain_platform.persistence.timescale import repositories as ts_repos
from onchain_platform.transport import state_cache

logger = structlog.get_logger(__name__)

_SOURCE = "projection_engine:poll:60s"


def compute_liquidity_usd(
    reserve0: str,
    reserve1: str,
    price0: Decimal | None,
    price1: Decimal | None,
) -> str | None:
    """Compute USD value of both reserves (Decimal math, DOC-008).

    Returns a Decimal-as-string, or None if any required price is unknown
    (a pair with an unknown quote price has no defensible USD liquidity).
    """
    if price0 is None or price1 is None:
        return None
    try:
        r0 = Decimal(reserve0)
        r1 = Decimal(reserve1)
    except Exception:  # malformed reserve string
        return None
    return str(r0 * price0 + r1 * price1)


async def run_snapshot_creation(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    chain_id: int,
    clock: Callable[[], datetime],
    price_oracle: PriceOracle | None = None,
) -> int:
    """Create a snapshot for every active pair, returning how many were written.

    "active" = a pair with a live StateProjection in Redis (it has been
    observed by the projection engine). Pairs without a Redis state are
    skipped.

    When `price_oracle` is provided, each snapshot's `liquidity_usd` is
    populated from the pair's token prices (Decimal math). Without an oracle,
    `liquidity_usd` stays None (M5 behavior) — pass a real oracle once prices
    are available (TD-1 / Phase 6).
    """
    now = clock()
    created = 0

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        pairs, _ = await entity_repositories.list_pairs(session)

    for pair in pairs:
        state = await state_cache.load_state(redis_client, chain_id, pair.pool_address)
        if state is None:
            # No live state yet — nothing to snapshot. Expected for a
            # newly-seen pair before its first swap/projection.
            continue

        liquidity_usd = None
        if price_oracle is not None:
            price0 = await price_oracle.get_price(pair.base_token_id.rsplit(":", 1)[-1], now)
            price1 = await price_oracle.get_price(pair.quote_token_id.rsplit(":", 1)[-1], now)
            liquidity_usd = compute_liquidity_usd(state.reserve0, state.reserve1, price0, price1)

        snapshot = ObservationSnapshot(
            schema_version="1.0",
            snapshot_id=f"{pair.canonical_id}|{now.isoformat()}|{_SOURCE}",
            entity_id=pair.canonical_id,
            chain_id=chain_id,
            snapshot_timestamp=now,
            observed_at=state.computed_at,
            ingested_at=now,
            source=_SOURCE,
            reserve0=state.reserve0,
            reserve1=state.reserve1,
            price=state.price,
            liquidity_usd=liquidity_usd,
        )

        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            inserted = await ts_repos.save_snapshot(session, snapshot)
        created += int(inserted)

    logger.info("snapshot_job_done", created=created, chain_id=chain_id)
    return created
