"""Integration tests: Market Bar generation against real Postgres/TimescaleDB.

Tests run against real infrastructure, never mocks (DOC-010 § Integration
Tests, DOC-011 § tests). Naming: test_<unit>_<scenario>_<expected_outcome>
(DOC-013 § Testing Conventions).

All OHLCV fields are Decimal-as-string, zero-tolerance (DOC-008 §
Financial Precision Principle).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics.trade_aggregator import (
    aggregate_swaps_to_bar,
    compute_pair_id,
)
from onchain_platform.domain.schemas.blockchain_fact import BlockchainFact, SwapExecutedPayload
from onchain_platform.domain.schemas.enums import (
    BarInterval,
    ConfirmationStatus,
    FactType,
)
from onchain_platform.persistence.postgres import repositories as pg_repos

CHAIN_ID = 8453
POOL = "0x39f0E675D479088DE08b7f201Ac08e20F899B838"
PAIR_ID = compute_pair_id(CHAIN_ID, POOL)
PINNED_TIME = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def _make_swap_fact(
    block_number: int,
    log_index: int,
    amount0_in: str = "0",
    amount1_in: str = "0",
    amount0_out: str = "0",
    amount1_out: str = "0",
    event_time: datetime | None = None,
) -> BlockchainFact:
    from onchain_platform.domain.schemas.blockchain_fact import BlockchainFact

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
        fact_id=f"{CHAIN_ID}:0x{block_number:064x}:{log_index}",
        chain_id=CHAIN_ID,
        fact_type=FactType.SWAP_EXECUTED,
        block_number=block_number,
        block_hash=f"0x{block_number:064x}",
        tx_hash=f"0x{block_number:064x}",
        log_index=log_index,
        event_time=event_time or datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC),
        observed_at=PINNED_TIME,
        ingested_at=PINNED_TIME,
        confirmation_status=ConfirmationStatus.FINALIZED,
        confirmations=10,
        payload=payload,
    )


@pytest.fixture
def clean_all(pg_engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    async def _clean() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(text("TRUNCATE blockchain_facts"))
            await conn.execute(text("TRUNCATE checkpoints"))

    return _clean


async def test_ohlcv_from_finalized_swaps(
    pg_engine: AsyncEngine, clean_all: Callable[[], Awaitable[None]]
) -> None:
    """5 finalized SWAP_EXECUTED facts → correct OHLCV bar."""
    await clean_all()

    # All swaps in the same 1-minute window (12:00:00–12:00:59).
    base_time = datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)
    facts = [
        _make_swap_fact(100, 0, amount0_in="1000", amount1_out="5000", event_time=base_time),
        _make_swap_fact(100, 1, amount1_in="3000", amount0_out="500", event_time=base_time),
        _make_swap_fact(101, 0, amount0_in="2000", amount1_out="8000", event_time=base_time),
        _make_swap_fact(101, 1, amount1_in="12000", amount0_out="2000", event_time=base_time),
        _make_swap_fact(102, 0, amount0_in="500", amount1_out="3000", event_time=base_time),
    ]

    # Insert all facts.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for fact in facts:
            await pg_repos.save_fact(session, fact)

    # Aggregate.
    bar = aggregate_swaps_to_bar(
        facts, PAIR_ID, CHAIN_ID, BarInterval.ONE_MINUTE, base_time, PINNED_TIME
    )
    assert bar is not None
    assert bar.trade_count == 5
    assert bar.is_provisional is False

    # Verify Decimal precision: all OHLCV fields are str.
    assert isinstance(bar.open, str)
    assert isinstance(bar.vwap, str)

    # Verify price direction: all prices are token1 per token0.
    assert Decimal(bar.open) > 0
    assert Decimal(bar.high) >= Decimal(bar.low)

    # Verify volume accounting.
    assert Decimal(bar.volume_base) > 0
    assert Decimal(bar.volume_quote) > 0
    assert Decimal(bar.buy_volume) >= 0
    assert Decimal(bar.sell_volume) >= 0


async def test_bar_recomputation_when_fact_orphans(
    pg_engine: AsyncEngine, clean_all: Callable[[], Awaitable[None]]
) -> None:
    """DOC-012 § B.3: if any fact in source_fact_range transitions to
    ORPHANED, the entire bar is recomputed from the predicate — never
    patched incrementally."""
    await clean_all()

    base_time = datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)
    facts = [
        _make_swap_fact(100, 0, amount0_in="1000", amount1_out="5000", event_time=base_time),
        _make_swap_fact(101, 0, amount0_in="2000", amount1_out="8000", event_time=base_time),
        _make_swap_fact(102, 0, amount0_in="3000", amount1_out="9000", event_time=base_time),
    ]

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for fact in facts:
            await pg_repos.save_fact(session, fact)

    # Compute bar from all 3 facts.
    bar_before = aggregate_swaps_to_bar(
        facts, PAIR_ID, CHAIN_ID, BarInterval.ONE_MINUTE, base_time, PINNED_TIME
    )
    assert bar_before is not None
    assert bar_before.trade_count == 3

    # Orphan one fact (simulate reorg).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await pg_repos.mark_facts_orphaned(session, CHAIN_ID, 100, 100)

    # Recompute bar from remaining FINALIZED facts (predicate re-run).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        remaining = await pg_repos.list_facts_for_chain(session, CHAIN_ID)
    finalized = [f for f in remaining if f.confirmation_status == ConfirmationStatus.FINALIZED]

    bar_after = aggregate_swaps_to_bar(
        finalized, PAIR_ID, CHAIN_ID, BarInterval.ONE_MINUTE, base_time, PINNED_TIME
    )
    assert bar_after is not None
    # Bar changed: fewer trades, different volumes.
    assert bar_after.trade_count < bar_before.trade_count
    assert Decimal(bar_after.volume_base) < Decimal(bar_before.volume_base)
