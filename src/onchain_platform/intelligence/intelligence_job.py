"""Intelligence scan job — APScheduler callback for risk analysis.

Runs the full intelligence pipeline: filter → GoPlus → risk rules →
insights. Respects rate limits and daily quota.

DOC-013 § Dependency & Composition: all dependencies injected via
callback parameters. main.py (composition root, exempt from contracts)
wires the actual implementations.
"""

from collections.abc import Callable
from datetime import datetime

import redis.asyncio as redis
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.intelligence.filter import select_tokens_for_scan
from onchain_platform.intelligence.goplus_client import GoPlusClient
from onchain_platform.intelligence.insight_generator import generate_insights
from onchain_platform.intelligence.risk_rules import extract_risk_signals
from onchain_platform.persistence.postgres import (
    outcomes_insights,
)

logger = structlog.get_logger(__name__)


async def run_intelligence_scan(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    chain_id: int,
    clock: Callable[[], datetime],
) -> None:
    """Full intelligence scan: filter → GoPlus → risk → insights.

    Respects rate limits (token bucket) and daily quota (28,000 CU).
    Graceful degradation: if GoPlus fails for a token, log warning and
    continue to next token (DOC-013: no cascade failures).
    """
    now = clock()

    # Step 1: Filter — select tokens to scan.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        pool_addresses = await select_tokens_for_scan(session, redis_client, chain_id, clock)

    if not pool_addresses:
        logger.info("intelligence_scan_no_candidates", chain_id=chain_id)
        return

    logger.info(
        "intelligence_scan_starting",
        chain_id=chain_id,
        candidate_count=len(pool_addresses),
    )

    # Step 2: For each candidate, fetch GoPlus data → risk → insights.
    goplus = GoPlusClient(redis_client)
    scanned = 0
    errors = 0

    try:
        for pool_address in pool_addresses:
            try:
                await _process_single_token(
                    pg_engine, redis_client, goplus, chain_id, pool_address, now
                )
                scanned += 1
            except Exception as exc:
                # Graceful degradation: log warning, continue to next token.
                errors += 1
                logger.warning(
                    "intelligence_scan_token_failed",
                    chain_id=chain_id,
                    pool_address=pool_address,
                    error=str(exc),
                )
    finally:
        await goplus.close()

    # Mark scanned tokens in Redis (24h TTL for dedup).
    for addr in pool_addresses[:scanned]:
        cache_key = f"goplus_scanned:{chain_id}:{addr.lower()}"
        await redis_client.set(cache_key, "1", ex=86400)

    logger.info(
        "intelligence_scan_complete",
        chain_id=chain_id,
        scanned=scanned,
        errors=errors,
    )


async def _process_single_token(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    goplus: GoPlusClient,
    chain_id: int,
    pool_address: str,
    now: datetime,
) -> None:
    """Process a single token: fetch GoPlus → extract signals → compute
    score → generate insights → persist."""
    # Fetch GoPlus data (cached in Redis for 24h).
    goplus_data = await goplus.get_token_security(chain_id, pool_address)
    if goplus_data is None:
        logger.info(
            "intelligence_no_goplus_data",
            chain_id=chain_id,
            pool_address=pool_address,
        )
        return

    # Extract risk signals.
    signals = extract_risk_signals(goplus_data)

    # Generate insights.
    from onchain_platform.domain.ids import pair_canonical_id

    entity_id = pair_canonical_id(chain_id, pool_address)
    insights = generate_insights(entity_id, signals, generated_at=now)

    # Persist insights.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for insight in insights:
            await outcomes_insights.save_insight(session, insight)

    if insights:
        logger.info(
            "intelligence_insights_persisted",
            entity_id=entity_id,
            insight_count=len(insights),
            risk_score=signals.risk_score,
        )
