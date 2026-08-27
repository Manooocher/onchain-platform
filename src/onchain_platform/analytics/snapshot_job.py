"""Observation snapshot production job (Milestone 5 gap / TD-3, TD-1).

Creates an ObservationSnapshot for every active pair on an interval, with
domain-aware liquidity_usd: pools are classified by quote token (USDC/WETH/
exotic), and the multi-source price oracle resolves the quote leg's USD value
with confidence tracking (TD-1 Phases 1-4).

Design:
- Reads active pairs from Postgres (list_pairs).
- Loads each pair's live StateProjection from Redis (DOC-012 § B.2).
- Classifies the pool (analytics.pool_classifier) to pick the liquidity_usd
  formula and quote-token type.
- Resolves the quote price via the injected MultiPriceOracle (STATIC for
  stablecoin, CHAINLINK for WETH, NULL for exotic).
- Emits a snapshot with liquidity_usd + source + confidence + quote type.

Determinism (DOC-013): `clock` is injected (no wall-clock here). analytics/
may import domain + persistence + transport (cross-cutting infra).
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import redis.asyncio as redis
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics.pool_classifier import classify_pool
from onchain_platform.domain.interfaces.price_oracle import (
    PoolClassification,
    PriceResult,
)
from onchain_platform.domain.schemas.enums import QuoteTokenType
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.postgres import entity_repositories
from onchain_platform.persistence.timescale import repositories as ts_repos
from onchain_platform.transport import state_cache

logger = structlog.get_logger(__name__)

_SOURCE = "projection_engine:poll:60s"


class PoolOracle(Protocol):
    """Minimal oracle interface the snapshot job needs (injectable)."""

    async def get_pool_result(
        self,
        pool_reserves: tuple[str, str],
        pool_class: PoolClassification,
        as_of: datetime,
    ) -> PriceResult: ...


ClassifierFn = Callable[[str, str, str], PoolClassification]


def _token_address(canonical_token_id: str) -> str:
    """Extract the checksummed address from a token canonical ID."""
    return canonical_token_id.rsplit(":", 1)[-1]


def compute_liquidity_usd(
    reserve0: str,
    reserve1: str,
    price0: Decimal | None,
    price1: Decimal | None,
) -> str | None:
    """Compute USD value of both reserves (Decimal math, DOC-008).

    A generic USD estimate of two legs' reserves; returns None if either
    price is unknown. The snapshot job now uses the domain-aware
    liquidity_usd_for_quote; this helper remains for callers that have both
    leg prices directly.
    """
    if price0 is None or price1 is None:
        return None
    try:
        r0 = Decimal(reserve0)
        r1 = Decimal(reserve1)
    except Exception:  # malformed reserve string
        return None
    return str(r0 * price0 + r1 * price1)


def liquidity_usd_for_quote(
    reserves: tuple[str, str],
    pool_class: PoolClassification,
    quote_result: PriceResult,
) -> tuple[str | None, str | None, float | None]:
    """Compute liquidity_usd + provenance for a classified pool.

    Domain-aware formulas (TD-1):
    - Stablecoin quote (USDC/USDT/DAI): symmetric — the quote reserve is USD,
      so liquidity ≈ reserve_quote * 2 (STATIC confidence from the result).
    - WETH quote: liquidity ≈ reserve_weth * eth_price * 2 (CHAINLINK/DEX).
    - Exotic: NULL, confidence 0.

    Returns (liquidity_usd as str|None, source as str|None, confidence).
    """
    quote = pool_class.quote_token_type
    if quote == QuoteTokenType.OTHER or quote_result.price_usd is None:
        return (None, None, 0.0)

    # The quote leg reserve is the one denominated by quote_result. We know
    # which token is the quote (pool_class.quote_token_address); find its
    # reserve from the pair of reserves by matching the pool's token order.
    quote_reserve = _reserve_for_quote(reserves, pool_class)
    if quote_reserve is None:
        return (None, None, 0.0)

    # liquidity_usd = quote_reserve * quote_price_usd * 2 (symmetric pool).
    import decimal

    try:
        usd = decimal.Decimal(quote_reserve) * quote_result.price_usd
        usd = usd * decimal.Decimal(2)
    except decimal.InvalidOperation:
        return (None, None, 0.0)

    return str(usd), quote_result.source.value, quote_result.confidence


def _reserve_for_quote(reserves: tuple[str, str], pool_class: PoolClassification) -> str | None:
    """Return the reserve of the pool's quote leg.

    If the quote token is token0, return reserve0; if token1, reserve1. When
    the quote is the stablecoin/WETH leg we need its own reserve. We infer leg
    by address comparison against the canonical token ids.
    """
    # pool_class.token0/token1 are the raw addresses; reserves are ordered by
    # the pool's token0/token1 order (state corresponds to pair base/quote).
    if pool_class.quote_token_address == pool_class.token0:
        return reserves[0]
    if pool_class.quote_token_address == pool_class.token1:
        return reserves[1]
    return None


async def run_snapshot_creation(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    chain_id: int,
    clock: Callable[[], datetime],
    *,
    oracle: PoolOracle | None = None,
    classifier: ClassifierFn = classify_pool,
) -> int:
    """Create a snapshot for every active pair, returning how many were written.

    `oracle` is a MultiPriceOracle-like object exposing get_pool_result.
    When None, liquidity_usd stays None (M5 fallback).
    `classifier` is the pure pool-classification callable (injectable).
    """
    now = clock()
    created = 0

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        pairs, _ = await entity_repositories.list_pairs(session)

    for pair in pairs:
        state = await state_cache.load_state(redis_client, chain_id, pair.pool_address)
        if state is None:
            continue

        liquidity_usd = None
        source: str | None = None
        confidence: float | None = None
        quote_type: str | None = None

        token0 = _token_address(pair.base_token_id)
        token1 = _token_address(pair.quote_token_id)
        pool_class = classifier(pair.pool_address, token0, token1)
        quote_type = pool_class.quote_token_type.value

        if oracle is not None:
            quote_result = await oracle.get_pool_result(
                (state.reserve0, state.reserve1), pool_class, now
            )
            liquidity_usd, source, confidence = liquidity_usd_for_quote(
                (state.reserve0, state.reserve1), pool_class, quote_result
            )

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
            liquidity_usd_source=source,
            liquidity_usd_confidence=confidence,
            quote_token_type=quote_type,
        )

        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            inserted = await ts_repos.save_snapshot(session, snapshot)
        created += int(inserted)

    logger.info("snapshot_job_done", created=created, chain_id=chain_id)
    return created
