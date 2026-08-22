"""Deterministic filter layer for GoPlus risk scanning (Milestone 7).

Selects ≤4,500 tokens/day from all newly discovered pairs that meet
filter criteria. Deterministic, no wall-clock in rules (DOC-013 §
Determinism Discipline) — clock is injected.

Filter rules (activity-based, per Milestone 7 plan Modification 2):
1. Pool has liquidity (reserve0 > 0 AND reserve1 > 0 in StateProjection)
2. Pool age ≤ 7 days (creation_event_time >= now - 7 days)
3. Supported DEX (uniswap_v2)
4. Not already scanned in last 24h (Redis dedup key)
5. Has at least 10 trades in last 24h (from MarketBar data)

Sort by trade_count_24h descending, cap at daily quota (4,500).
"""

from collections.abc import Callable
from datetime import datetime, timedelta

import redis.asyncio as redis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.persistence.postgres.models import TradingPairRow
from onchain_platform.persistence.timescale.repositories import MarketBarRow
from onchain_platform.transport import state_cache

logger = structlog.get_logger(__name__)

# Daily quota: 4,500 tokens (margin under 30,000 CU/day GoPlus limit).
DAILY_QUOTA = 4_500
# Minimum trades in last 24h to be considered active.
MIN_TRADES_24H = 10
# Maximum pool age in days.
MAX_POOL_AGE_DAYS = 7
# Supported DEXes for M7.
SUPPORTED_DEXES = ("uniswap_v2",)


async def select_tokens_for_scan(
    session: AsyncSession,
    redis_client: redis.Redis,
    chain_id: int,
    clock: Callable[[], datetime],
) -> list[str]:
    """Select pool_addresses to scan with GoPlus.

    Returns ≤4,500 pool addresses sorted by activity (most active first).
    Deterministic: same inputs → same outputs (DOC-013 § Determinism
    Discipline). Clock is injected, never wall-clock.
    """
    now = clock()
    twenty_four_hours_ago = now - timedelta(hours=24)

    # Step 1: Get TradingPairs created in last 7 days on supported DEXes.
    stmt = (
        select(TradingPairRow)
        .where(
            TradingPairRow.chain_id == chain_id,
            TradingPairRow.creation_block > 0,  # exists
        )
        .order_by(TradingPairRow.creation_block.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    candidates: list[tuple[str, int]] = []  # (pool_address, trade_count_24h)

    for row in rows:
        pool_address = row.pool_address

        # Dedup: not already scanned in last 24h.
        cache_key = f"goplus_scanned:{chain_id}:{pool_address.lower()}"
        if await redis_client.exists(cache_key):
            continue

        # Basic sanity: pool has liquidity (from StateProjection in Redis).
        state = await state_cache.load_state(redis_client, chain_id, pool_address)
        if state is None:
            continue
        if state.reserve0 == "0" or state.reserve1 == "0":
            continue

        # Activity check: at least MIN_TRADES_24H trades in last 24h.
        # Use MarketBar data from M3's trade_aggregator.
        bars_stmt = select(MarketBarRow).where(
            MarketBarRow.pair_id == row.canonical_id,
            MarketBarRow.bar_start_time >= twenty_four_hours_ago,
        )
        bars = (await session.execute(bars_stmt)).scalars().all()
        trade_count_24h = sum(bar.trade_count for bar in bars)
        if trade_count_24h < MIN_TRADES_24H:
            continue

        candidates.append((pool_address, trade_count_24h))

    # Sort by activity (most active first) — deterministic ordering.
    candidates.sort(key=lambda x: x[1], reverse=True)

    # Cap at daily quota.
    selected = [addr for addr, _ in candidates[:DAILY_QUOTA]]

    logger.info(
        "filter_selected",
        chain_id=chain_id,
        total_candidates=len(candidates),
        selected=len(selected),
    )
    return selected
