"""Unit tests: Projection Engine (DOC-012 § B.2, DOC-006 § Data Lifecycle).

All reserve/price fields are Decimal-as-string, zero-tolerance (DOC-008 §
Financial Precision). Price is always token1 per token0 (DOC-012 § B.2).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from onchain_platform.analytics.projection_engine import (
    _compute_price,
    update_projection,
)
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    LiquidityAddedPayload,
    LiquidityRemovedPayload,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType
from onchain_platform.domain.schemas.state_projection import StateProjection

MockDeps = tuple[AsyncMock, AsyncMock, MagicMock]

CHAIN_ID = 8453
POOL = "0x39f0E675D479088DE08b7f201Ac08e20F899B838"
TOKEN0 = "0x4200000000000000000000000000000000000006"
TOKEN1 = "0x833589FCdbe0E8C5a3c3f0e0b2F5b5a5A5A5a5a5"
PINNED = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def test_compute_price_basic() -> None:
    # Decimal('2000') / Decimal('1000') = Decimal('2'), str() → "2"
    # (no trailing zero — natural Decimal representation, M3 precedent).
    assert _compute_price(Decimal("1000"), Decimal("2000")) == "2"
    expected = str(Decimal("3333") / Decimal("1000"))
    assert _compute_price(Decimal("1000"), Decimal("3333")) == expected


def test_compute_price_zero_reserve0() -> None:
    assert _compute_price(Decimal("0"), Decimal("1000")) == "0"


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
        confirmation_status=ConfirmationStatus.PENDING,
        confirmations=0,
        payload=payload,
    )


def _make_liquidity_fact(
    fact_type_str: str,
    amount0: str,
    amount1: str,
    block_number: int = 200,
) -> BlockchainFact:
    payload: LiquidityAddedPayload | LiquidityRemovedPayload
    if fact_type_str == "LIQUIDITY_ADDED":
        payload = LiquidityAddedPayload(
            fact_type="LIQUIDITY_ADDED",
            pool_address=POOL,
            provider="0x" + "33" * 20,
            amount0=amount0,
            amount1=amount1,
            liquidity_delta=amount0,
        )
        ft = FactType.LIQUIDITY_ADDED
    else:
        payload = LiquidityRemovedPayload(
            fact_type="LIQUIDITY_REMOVED",
            pool_address=POOL,
            provider="0x" + "33" * 20,
            amount0=amount0,
            amount1=amount1,
            liquidity_delta=amount0,
        )
        ft = FactType.LIQUIDITY_REMOVED
    return BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{block_number:064x}:0",
        chain_id=CHAIN_ID,
        fact_type=ft,
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


@pytest.fixture
def mock_deps() -> tuple[AsyncMock, AsyncMock, MagicMock]:
    """Mock Redis, session, and entity repos for unit tests."""
    redis_mock = AsyncMock()
    session_mock = AsyncMock()
    # Mock load_state to return None (no existing state).
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock()
    # Mock entity_repos.get_trading_pair to return a mock TradingPair.
    pair_mock = MagicMock()
    pair_mock.base_token_id = f"eip155:{CHAIN_ID}/token:{TOKEN0}"
    pair_mock.quote_token_id = f"eip155:{CHAIN_ID}/token:{TOKEN1}"
    return redis_mock, session_mock, pair_mock


@pytest.mark.asyncio
async def test_swap_updates_reserves_and_price(mock_deps: MockDeps) -> None:
    """Swap: amount0_in=100, amount1_out=180. Starting from (1000, 2000).
    reserve0 = 1000 + 100 = 1100, reserve1 = 2000 - 180 = 1820.
    price = 1820/1100."""
    redis_mock, session_mock, pair_mock = mock_deps

    # Mock load_state to return initial state.
    initial = StateProjection(
        entity_id=f"eip155:{CHAIN_ID}/pair:{POOL}",
        chain_id=CHAIN_ID,
        as_of_block=99,
        as_of_fact_id="old",
        computed_at=PINNED,
        reserve0="1000",
        reserve1="2000",
        price="2.0",
    )
    redis_mock.get = AsyncMock(return_value=initial.model_dump_json().encode())

    # Mock entity_repos.get_trading_pair.
    import onchain_platform.persistence.postgres.entity_repositories as entity_repos

    original = entity_repos.get_trading_pair
    entity_repos.get_trading_pair = AsyncMock(return_value=pair_mock)

    fact = _make_swap_fact(amount0_in="100", amount1_out="180")

    try:
        await update_projection(session_mock, redis_mock, fact, lambda: PINNED)
    finally:
        entity_repos.get_trading_pair = original

    # Verify Redis.set was called with updated state.
    redis_mock.set.assert_called_once()
    saved_json = redis_mock.set.call_args[0][1]
    saved = StateProjection.model_validate_json(saved_json)
    assert saved.reserve0 == "1100"
    assert saved.reserve1 == "1820"
    assert Decimal(saved.price) == Decimal("1820") / Decimal("1100")


@pytest.mark.asyncio
async def test_liquidity_add_increases_reserves(mock_deps: MockDeps) -> None:
    """LIQUIDITY_ADDED: amount0=500, amount1=1000. Starting from (1000, 2000).
    reserve0 = 1500, reserve1 = 3000. Price unchanged (proportional add)."""
    redis_mock, session_mock, pair_mock = mock_deps

    initial = StateProjection(
        entity_id=f"eip155:{CHAIN_ID}/pair:{POOL}",
        chain_id=CHAIN_ID,
        as_of_block=99,
        as_of_fact_id="old",
        computed_at=PINNED,
        reserve0="1000",
        reserve1="2000",
        price="2.0",
    )
    redis_mock.get = AsyncMock(return_value=initial.model_dump_json().encode())

    import onchain_platform.persistence.postgres.entity_repositories as entity_repos

    original = entity_repos.get_trading_pair
    entity_repos.get_trading_pair = AsyncMock(return_value=pair_mock)

    fact = _make_liquidity_fact("LIQUIDITY_ADDED", "500", "1000")

    try:
        await update_projection(session_mock, redis_mock, fact, lambda: PINNED)
    finally:
        entity_repos.get_trading_pair = original

    redis_mock.set.assert_called_once()
    saved_json = redis_mock.set.call_args[0][1]
    saved = StateProjection.model_validate_json(saved_json)
    assert saved.reserve0 == "1500"
    assert saved.reserve1 == "3000"
    # Price unchanged (proportional add).
    assert Decimal(saved.price) == Decimal("2.0")


@pytest.mark.asyncio
async def test_liquidity_remove_decreases_reserves(mock_deps: MockDeps) -> None:
    """LIQUIDITY_REMOVED: amount0=200, amount1=400. Starting from (1000, 2000).
    reserve0 = 800, reserve1 = 1600."""
    redis_mock, session_mock, pair_mock = mock_deps

    initial = StateProjection(
        entity_id=f"eip155:{CHAIN_ID}/pair:{POOL}",
        chain_id=CHAIN_ID,
        as_of_block=99,
        as_of_fact_id="old",
        computed_at=PINNED,
        reserve0="1000",
        reserve1="2000",
        price="2.0",
    )
    redis_mock.get = AsyncMock(return_value=initial.model_dump_json().encode())

    import onchain_platform.persistence.postgres.entity_repositories as entity_repos

    original = entity_repos.get_trading_pair
    entity_repos.get_trading_pair = AsyncMock(return_value=pair_mock)

    fact = _make_liquidity_fact("LIQUIDITY_REMOVED", "200", "400")

    try:
        await update_projection(session_mock, redis_mock, fact, lambda: PINNED)
    finally:
        entity_repos.get_trading_pair = original

    redis_mock.set.assert_called_once()
    saved_json = redis_mock.set.call_args[0][1]
    saved = StateProjection.model_validate_json(saved_json)
    assert saved.reserve0 == "800"
    assert saved.reserve1 == "1600"


@pytest.mark.asyncio
async def test_pair_created_does_not_update_projection(mock_deps: MockDeps) -> None:
    """PAIR_CREATED doesn't affect reserves — projection engine skips it."""
    redis_mock, session_mock, _ = mock_deps

    from onchain_platform.domain.schemas.blockchain_fact import PairCreatedPayload

    fact = BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{'aa' * 32}:0",
        chain_id=CHAIN_ID,
        fact_type=FactType.PAIR_CREATED,
        block_number=100,
        block_hash=f"0x{'bb' * 32}",
        tx_hash=f"0x{'aa' * 32}",
        log_index=0,
        event_time=datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC),
        observed_at=PINNED,
        ingested_at=PINNED,
        confirmation_status=ConfirmationStatus.PENDING,
        confirmations=0,
        payload=PairCreatedPayload(
            fact_type="PAIR_CREATED",
            pair_address=POOL,
            token0_address=TOKEN0,
            token1_address=TOKEN1,
            dex="uniswap_v2",
        ),
    )

    await update_projection(AsyncMock(), redis_mock, fact, lambda: PINNED)
    redis_mock.set.assert_not_called()
