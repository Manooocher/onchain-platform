"""Projection Engine — consumes finalized facts, updates StateProjection
in Redis (DOC-012 § B.2, DOC-006 § Data Lifecycle).

DOC-006: "State is continuously updated from Blockchain Facts."
DOC-012 § B.2: "The live, mutable, continuously-recomputed read model.
Never persisted as its own historical table — served from Redis cache."

All intermediate math uses Decimal (DOC-008 § Financial Precision). Price
is always token1 per token0 (DOC-012 § B.2). A single float conversion
invalidates the entire computation.

Determinism (DOC-013 § Determinism Discipline): no wall-clock reads —
computed_at comes from the injected clock.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, getcontext

import redis.asyncio as redis
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.ids import pair_canonical_id
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    LiquidityAddedPayload,
    LiquidityRemovedPayload,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType
from onchain_platform.domain.schemas.state_projection import StateProjection
from onchain_platform.persistence.postgres import (
    entity_repositories as entity_repos,
)
from onchain_platform.persistence.postgres import (
    repositories as fact_repos,
)
from onchain_platform.transport import state_cache

# Match NUMERIC(78,0) for uint256 inputs (DOC-014 § Type Mapping Rules).
getcontext().prec = 78

logger = structlog.get_logger(__name__)


async def _get_token_ordering(
    session: AsyncSession, chain_id: int, pool_address: str
) -> tuple[str, str] | None:
    """Look up token0/token1 ordering from TradingPair entity.

    Returns (token0_address, token1_address) or None if TradingPair not
    yet resolved (entity resolution lag). The projection engine skips
    pools without a resolved TradingPair — it will be caught on the next
    rebuild.
    """
    pair_cid = pair_canonical_id(chain_id, pool_address)
    pair = await entity_repos.get_trading_pair(session, pair_cid)
    if pair is None:
        return None
    # base_token_id = token0, quote_token_id = token1 (from PairCreated).
    # Extract addresses from canonical IDs.
    token0 = pair.base_token_id.split(":")[-1]
    token1 = pair.quote_token_id.split(":")[-1]
    return (token0, token1)


def _compute_price(reserve0: Decimal, reserve1: Decimal) -> str:
    """Price = reserve1 / reserve0 (token1 per token0, DOC-012 § B.2).

    Returns Decimal-as-string. If reserve0 is 0, price is "0" (defensive).
    """
    if reserve0 == 0:
        return "0"
    return str(reserve1 / reserve0)


async def update_projection(
    session: AsyncSession,
    r: redis.Redis,
    fact: BlockchainFact,
    clock: Callable[[], datetime],
) -> None:
    """Apply a finalized fact to the affected pool's StateProjection.

    Called by the handler in main.py after a fact is persisted. Only
    processes facts that affect pool reserves: SWAP_EXECUTED,
    LIQUIDITY_ADDED, LIQUIDITY_REMOVED.
    """
    if fact.fact_type == FactType.SWAP_EXECUTED:
        assert isinstance(fact.payload, SwapExecutedPayload)
        pool_address: str | None = fact.payload.pool_address
    elif fact.fact_type in (FactType.LIQUIDITY_ADDED, FactType.LIQUIDITY_REMOVED):
        assert isinstance(fact.payload, (LiquidityAddedPayload, LiquidityRemovedPayload))
        pool_address = fact.payload.pool_address
    else:
        return  # PAIR_CREATED doesn't affect reserves

    if pool_address is None:
        return

    # Load current state from Redis (or create zero-state).
    current = await state_cache.load_state(r, fact.chain_id, pool_address)
    if current is None:
        current = StateProjection(
            entity_id=pair_canonical_id(fact.chain_id, pool_address),
            chain_id=fact.chain_id,
            as_of_block=0,
            as_of_fact_id="",
            computed_at=clock(),
            reserve0="0",
            reserve1="0",
            price="0",
        )

    # Look up token ordering from TradingPair entity.
    ordering = await _get_token_ordering(session, fact.chain_id, pool_address)
    if ordering is None:
        logger.warning(
            "projection_skip_no_trading_pair",
            chain_id=fact.chain_id,
            pool_address=pool_address,
            fact_id=fact.fact_id,
        )
        return

    r0 = Decimal(current.reserve0)
    r1 = Decimal(current.reserve1)

    if fact.fact_type == FactType.SWAP_EXECUTED:
        assert isinstance(fact.payload, SwapExecutedPayload)
        a0_in = Decimal(fact.payload.amount0_in)
        a1_in = Decimal(fact.payload.amount1_in)
        a0_out = Decimal(fact.payload.amount0_out)
        a1_out = Decimal(fact.payload.amount1_out)
        # Swap: reserves change based on in/out amounts.
        r0 = r0 + a0_in - a0_out
        r1 = r1 + a1_in - a1_out
    elif fact.fact_type == FactType.LIQUIDITY_ADDED:
        assert isinstance(fact.payload, LiquidityAddedPayload)
        r0 = r0 + Decimal(fact.payload.amount0)
        r1 = r1 + Decimal(fact.payload.amount1)
    elif fact.fact_type == FactType.LIQUIDITY_REMOVED:
        assert isinstance(fact.payload, LiquidityRemovedPayload)
        r0 = r0 - Decimal(fact.payload.amount0)
        r1 = r1 - Decimal(fact.payload.amount1)

    # Defensive: reserves should never go negative (DOC-014 CHECK constraint
    # on blockchain_facts amounts; pool math should prevent this).
    if r0 < 0 or r1 < 0:
        logger.warning(
            "projection_negative_reserves",
            chain_id=fact.chain_id,
            pool_address=pool_address,
            reserve0=str(r0),
            reserve1=str(r1),
            fact_id=fact.fact_id,
        )
        r0 = max(r0, Decimal(0))
        r1 = max(r1, Decimal(0))

    updated = StateProjection(
        entity_id=current.entity_id,
        chain_id=fact.chain_id,
        as_of_block=fact.block_number,
        as_of_fact_id=fact.fact_id,
        computed_at=clock(),
        reserve0=str(r0),
        reserve1=str(r1),
        price=_compute_price(r0, r1),
    )

    await state_cache.save_state(r, updated)
    logger.info(
        "projection_updated",
        chain_id=fact.chain_id,
        pool_address=pool_address,
        as_of_block=fact.block_number,
        reserve0=str(r0),
        reserve1=str(r1),
        price=updated.price,
    )


async def rebuild_from_facts(
    session: AsyncSession,
    r: redis.Redis,
    chain_id: int,
    clock: Callable[[], datetime],
) -> None:
    """Rebuild all StateProjections from FINALIZED facts.

    DOC-006: "State can always be reconstructed by replaying Facts."
    Called on startup to restore state from facts (or from last snapshot
    forward — future optimization).
    """
    # Clear existing state.
    keys = await state_cache.list_state_keys(r)
    for key in keys:
        parts = key.split(":")
        if len(parts) == 3:
            await state_cache.delete_state(r, int(parts[1]), parts[2])

    # Replay all FINALIZED facts in order.
    facts = await fact_repos.list_facts_for_chain(session, chain_id)
    finalized = [f for f in facts if f.confirmation_status == ConfirmationStatus.FINALIZED]
    finalized.sort(key=lambda f: (f.block_number, f.log_index))

    for fact in finalized:
        await update_projection(session, r, fact, clock)

    logger.info(
        "projection_rebuilt",
        chain_id=chain_id,
        facts_processed=len(finalized),
    )
