"""Integration tests: State Projection against real Redis + Postgres.

Tests run against real infrastructure, never mocks (DOC-010 § Integration
Tests, DOC-011 § tests). Naming: test_<unit>_<scenario>_<expected_outcome>
(DOC-013 § Testing Conventions).

All reserve/price fields are Decimal-as-string, zero-tolerance (DOC-008 §
Financial Precision).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics import projection_engine
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    LiquidityAddedPayload,
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

CHAIN_ID = 8453
POOL = "0x39f0E675D479088DE08b7f201Ac08e20F899B838"
TOKEN0 = "0x4200000000000000000000000000000000000006"
TOKEN1 = "0x833589FCdbe0E8C5a3c3f0e0b2F5b5a5A5A5a5a5"
PINNED = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture
def redis_client() -> redis.Redis:
    return redis.from_url(REDIS_URL)


_CleanFn = Callable[[], Awaitable[None]]


def _make_swap_fact(
    amount0_in: str = "0",
    amount1_in: str = "0",
    amount0_out: str = "0",
    amount1_out: str = "0",
    block_number: int = 100,
) -> BlockchainFact:
    payload = SwapExecutedPayload(
        fact_type="SWAP_EXECUTED",
        pool_address=POOL,
        sender="0x" + "11" * 20,
        recipient="0x" + "22" * 20,
        amount0_in=amount0_in,
        amount1_in=amount1_in,
        amount0_out=amount0_out,
        amount1_out=amount1_out,
    )
    return BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{block_number:064x}:0",
        chain_id=CHAIN_ID,
        fact_type=FactType.SWAP_EXECUTED,
        block_number=block_number,
        block_hash=f"0x{block_number:064x}",
        tx_hash=f"0x{block_number:064x}",
        log_index=0,
        event_time=datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC),
        observed_at=PINNED,
        ingested_at=PINNED,
        confirmation_status=ConfirmationStatus.FINALIZED,
        confirmations=10,
        payload=payload,
    )


def _make_liquidity_added_fact(
    amount0: str, amount1: str, block_number: int = 200
) -> BlockchainFact:
    payload = LiquidityAddedPayload(
        fact_type="LIQUIDITY_ADDED",
        pool_address=POOL,
        provider="0x" + "33" * 20,
        amount0=amount0,
        amount1=amount1,
        liquidity_delta=amount0,
    )
    return BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{block_number:064x}:0",
        chain_id=CHAIN_ID,
        fact_type=FactType.LIQUIDITY_ADDED,
        block_number=block_number,
        block_hash=f"0x{block_number:064x}",
        tx_hash=f"0x{block_number:064x}",
        log_index=0,
        event_time=datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC),
        observed_at=PINNED,
        ingested_at=PINNED,
        confirmation_status=ConfirmationStatus.FINALIZED,
        confirmations=10,
        payload=payload,
    )


async def _seed_trading_pair(session: AsyncSession) -> None:
    """Create Token + TradingPair entities so the projection engine can
    look up token ordering."""
    from onchain_platform.domain.entities.token import Token

    t0 = Token(
        canonical_id=token_canonical_id(CHAIN_ID, TOKEN0),
        chain_id=CHAIN_ID,
        contract_address=TOKEN0,
    )
    t1 = Token(
        canonical_id=token_canonical_id(CHAIN_ID, TOKEN1),
        chain_id=CHAIN_ID,
        contract_address=TOKEN1,
    )
    await entity_repos.save_token(session, t0)
    await entity_repos.save_token(session, t1)
    tp = TradingPair(
        canonical_id=pair_canonical_id(CHAIN_ID, POOL),
        chain_id=CHAIN_ID,
        dex="uniswap_v2",
        base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
        quote_token_id=token_canonical_id(CHAIN_ID, TOKEN1),
        pool_address=POOL,
        creation_block=100,
        creation_fact_id=f"{CHAIN_ID}:0x{'aa' * 32}:0",
    )
    await entity_repos.save_trading_pair(session, tp)


async def test_swap_updates_reserves_and_price(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    clean_entities: _CleanFn,
    clean_facts: _CleanFn,
) -> None:
    await clean_entities()
    await clean_facts()
    await redis_client.flushdb()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_trading_pair(session)

    # Seed initial state in Redis.
    initial = StateProjection(
        entity_id=pair_canonical_id(CHAIN_ID, POOL),
        chain_id=CHAIN_ID,
        as_of_block=99,
        as_of_fact_id="seed",
        computed_at=PINNED,
        reserve0="1000",
        reserve1="2000",
        price="2",
    )
    await state_cache.save_state(redis_client, initial)

    # Process swap: amount0_in=100, amount1_out=180.
    fact = _make_swap_fact(amount0_in="100", amount1_out="180")
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await projection_engine.update_projection(session, redis_client, fact, lambda: PINNED)

    loaded = await state_cache.load_state(redis_client, CHAIN_ID, POOL)
    assert loaded is not None
    assert loaded.reserve0 == "1100"
    assert loaded.reserve1 == "1820"
    assert Decimal(loaded.price) == Decimal("1820") / Decimal("1100")


async def test_liquidity_added_updates_reserves(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    clean_entities: _CleanFn,
    clean_facts: _CleanFn,
) -> None:
    await clean_entities()
    await clean_facts()
    await redis_client.flushdb()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_trading_pair(session)

    initial = StateProjection(
        entity_id=pair_canonical_id(CHAIN_ID, POOL),
        chain_id=CHAIN_ID,
        as_of_block=99,
        as_of_fact_id="seed",
        computed_at=PINNED,
        reserve0="1000",
        reserve1="2000",
        price="2",
    )
    await state_cache.save_state(redis_client, initial)

    fact = _make_liquidity_added_fact("500", "1000")
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await projection_engine.update_projection(session, redis_client, fact, lambda: PINNED)

    loaded = await state_cache.load_state(redis_client, CHAIN_ID, POOL)
    assert loaded is not None
    assert loaded.reserve0 == "1500"
    assert loaded.reserve1 == "3000"
    assert Decimal(loaded.price) == Decimal("2")


async def test_rebuild_from_facts_restores_state(
    pg_engine: AsyncEngine,
    redis_client: redis.Redis,
    clean_entities: _CleanFn,
    clean_facts: _CleanFn,
) -> None:
    await clean_entities()
    await clean_facts()
    await redis_client.flushdb()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_trading_pair(session)
        # Insert finalized facts.
        f1 = _make_swap_fact(amount0_in="100", amount1_out="180", block_number=100)
        f2 = _make_swap_fact(amount0_in="50", amount1_out="95", block_number=101)
        await fact_repos.save_fact(session, f1)
        await fact_repos.save_fact(session, f2)

    # Rebuild from facts.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await projection_engine.rebuild_from_facts(session, redis_client, CHAIN_ID, lambda: PINNED)

    loaded = await state_cache.load_state(redis_client, CHAIN_ID, POOL)
    assert loaded is not None
    # Two swaps: reserve0 = 0 + 100 + 50 = 150, reserve1 = 0 - 180 - 95
    # (negative → clamped to 0). Actually: starting from zero reserves,
    # swap adds amount0_in and subtracts amount1_out. With zero initial
    # reserves, the first swap: r0=0+100=100, r1=0-180=-180→0.
    # Second swap: r0=100+50=150, r1=0-95=-95→0.
    assert loaded.reserve0 == "150"
    assert loaded.reserve1 == "0"
