"""APScheduler integration for Feature computation (DOC-010 § Job Scheduling).

DOC-010: "APScheduler — Lightweight, in-process job scheduling."
Milestone 6: register Feature computation as a periodic job.

DOC-013 § Dependency & Composition: the scheduler does NOT import any
Capability — main.py wires the actual job functions.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics import feature_engine
from onchain_platform.domain.ids import pair_canonical_id
from onchain_platform.persistence.postgres import entity_repositories as entity_repos
from onchain_platform.persistence.timescale import repositories as ts_repos
from onchain_platform.transport import state_cache

logger = structlog.get_logger(__name__)


async def compute_features_job(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    chain_id: int,
    clock: Callable[[], datetime],
) -> None:
    """Compute Features for all active pools (those with StateProjection in Redis).

    Only pools with existing StateProjection are processed (avoids
    snapshotting empty pools — Milestone 5 § Open Decisions Q3).
    """
    keys = await state_cache.list_state_keys(redis_client)
    now = clock()

    for key in keys:
        # key format: state:{chain_id}:{pool_address}
        parts = key.split(":")
        if len(parts) != 3:
            continue
        pool_address = parts[2]
        entity_id = pair_canonical_id(chain_id, pool_address)

        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            pair = await entity_repos.get_trading_pair(session, entity_id)
            if pair is None:
                continue

            # Compute liquidity_growth_pct_1h.
            feat1 = await feature_engine.compute_liquidity_growth_pct_1h(
                session, entity_id, chain_id, now, now
            )
            if feat1 is not None:
                await ts_repos.save_feature(session, feat1)
                logger.info(
                    "feature_computed",
                    feature_name=feat1.feature_name,
                    entity_id=entity_id,
                    value=feat1.value,
                )

            # Compute price_momentum_zscore_1h.
            feat2 = await feature_engine.compute_price_momentum_zscore_1h(
                session, entity_id, chain_id, now, now
            )
            if feat2 is not None:
                await ts_repos.save_feature(session, feat2)
                logger.info(
                    "feature_computed",
                    feature_name=feat2.feature_name,
                    entity_id=entity_id,
                    value=feat2.value,
                )


def create_feature_scheduler(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    chain_id: int,
    clock: Callable[[], datetime],
    interval_seconds: int = 3600,
) -> AsyncIOScheduler:
    """Create and configure the APScheduler for Feature computation.

    DOC-010 § Job Scheduling: "Lightweight, in-process job scheduling."
    """
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        compute_features_job,
        "interval",
        seconds=interval_seconds,
        args=[pg_engine, redis_client, chain_id, clock],
        id="feature_computation",
        name="Feature computation (liquidity_growth_pct_1h, price_momentum_zscore_1h)",
        max_instances=1,
    )
    return scheduler
