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

import redis.asyncio as redis
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.postgres import entity_repositories
from onchain_platform.persistence.timescale import repositories as ts_repos
from onchain_platform.transport import state_cache

logger = structlog.get_logger(__name__)

_SOURCE = "projection_engine:poll:60s"


async def run_snapshot_creation(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    chain_id: int,
    clock: Callable[[], datetime],
) -> int:
    """Create a snapshot for every active pair, returning how many were written.

    "active" = a pair with a live StateProjection in Redis (it has been
    observed by the projection engine). Pairs without a Redis state are
    skipped.
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

        snapshot = ObservationSnapshot.create(
            entity_id=pair.canonical_id,
            chain_id=chain_id,
            snapshot_timestamp=now,
            observed_at=state.computed_at,
            ingested_at=now,
            source=_SOURCE,
            reserve0=state.reserve0,
            reserve1=state.reserve1,
            price=state.price,
        )

        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            inserted = await ts_repos.save_snapshot(session, snapshot)
        created += int(inserted)

    logger.info("snapshot_job_done", created=created, chain_id=chain_id)
    return created
