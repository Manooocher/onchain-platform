"""Unit tests: Trade Aggregator (DOC-006 § Market Data Pipeline, DOC-012 §
B.3).

All OHLCV fields are Decimal-as-string, zero-tolerance byte-identity
(DOC-008 § Financial Precision Principle). Price is always token1 per
token0 (DOC-012 § B.2). Buy/sell volume follows token0=base convention.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime
from decimal import Decimal

from onchain_platform.analytics.trade_aggregator import (
    aggregate_swaps_to_bar,
    bucket_start,
    compute_pair_id,
)
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import BarInterval, ConfirmationStatus, FactType

CHAIN_ID = 8453
POOL = "0x39f0E675D479088DE08b7f201Ac08e20F899B838"
PAIR_ID = f"eip155:{CHAIN_ID}/pair:{POOL}"
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
    """Create a SWAP_EXECUTED BlockchainFact with the given amounts."""
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


def test_compute_pair_id_format() -> None:
    assert compute_pair_id(8453, POOL) == PAIR_ID


def test_bucket_start_epoch_arithmetic() -> None:
    dt = datetime(2024, 4, 22, 12, 35, 55, tzinfo=UTC)
    assert bucket_start(dt, BarInterval.ONE_MINUTE) == datetime(2024, 4, 22, 12, 35, 0, tzinfo=UTC)
    assert bucket_start(dt, BarInterval.FIVE_MINUTES) == datetime(
        2024, 4, 22, 12, 35, 0, tzinfo=UTC
    )
    assert bucket_start(dt, BarInterval.ONE_HOUR) == datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)


def test_aggregate_empty_facts_returns_none() -> None:
    bar = aggregate_swaps_to_bar(
        [], PAIR_ID, CHAIN_ID, BarInterval.ONE_MINUTE, PINNED_TIME, PINNED_TIME
    )
    assert bar is None


def test_aggregate_single_swap_ohlcv() -> None:
    """One swap: user gave token0 (amount0_in=1000), got token1
    (amount1_out=5000). Price = 5000/1000 = 5.0 token1 per token0."""
    fact = _make_swap_fact(
        block_number=100,
        log_index=0,
        amount0_in="1000",
        amount1_out="5000",
    )
    bar = aggregate_swaps_to_bar(
        [fact], PAIR_ID, CHAIN_ID, BarInterval.ONE_MINUTE, PINNED_TIME, PINNED_TIME
    )
    assert bar is not None
    assert bar.open == "5"
    assert bar.high == "5"
    assert bar.low == "5"
    assert bar.close == "5"
    assert bar.volume_base == "1000"
    assert bar.volume_quote == "5000"
    assert bar.trade_count == 1
    assert bar.vwap == "5"
    assert bar.sell_volume == "1000"
    assert bar.buy_volume == "0"
    assert bar.is_provisional is False


def test_aggregate_two_swaps_ohlcv() -> None:
    """Two swaps in same bar: different prices."""
    fact1 = _make_swap_fact(
        block_number=100,
        log_index=0,
        amount0_in="1000",
        amount1_out="5000",
    )
    fact2 = _make_swap_fact(
        block_number=100,
        log_index=1,
        amount1_in="3000",
        amount0_out="500",
    )
    bar = aggregate_swaps_to_bar(
        [fact1, fact2], PAIR_ID, CHAIN_ID, BarInterval.ONE_MINUTE, PINNED_TIME, PINNED_TIME
    )
    assert bar is not None
    assert bar.open == "5"
    assert bar.close == "6"
    assert bar.high == "6"
    assert bar.low == "5"
    assert bar.trade_count == 2
    assert bar.volume_base == "1500"
    assert bar.sell_volume == "1000"
    assert bar.buy_volume == "500"


def test_aggregate_price_always_token1_per_token0() -> None:
    """Two swaps in same pair, opposite directions. Both prices must be
    token1 per token0 (Modification 1)."""
    fact1 = _make_swap_fact(
        block_number=100,
        log_index=0,
        amount0_in="1000",
        amount1_out="5000",
    )
    fact2 = _make_swap_fact(
        block_number=100,
        log_index=1,
        amount1_in="6000",
        amount0_out="1000",
    )
    bar = aggregate_swaps_to_bar(
        [fact1, fact2], PAIR_ID, CHAIN_ID, BarInterval.ONE_MINUTE, PINNED_TIME, PINNED_TIME
    )
    assert bar is not None
    assert bar.open == "5"
    assert bar.close == "6"
    assert Decimal(bar.open) < Decimal(bar.close)


def test_aggregate_vwap_weighted_correctly() -> None:
    """VWAP = (2.0*1000 + 4.0*3000) / (1000+3000) = 3.5."""
    fact1 = _make_swap_fact(
        block_number=100,
        log_index=0,
        amount0_in="1000",
        amount1_out="2000",
    )
    fact2 = _make_swap_fact(
        block_number=100,
        log_index=1,
        amount0_in="3000",
        amount1_out="12000",
    )
    bar = aggregate_swaps_to_bar(
        [fact1, fact2], PAIR_ID, CHAIN_ID, BarInterval.ONE_MINUTE, PINNED_TIME, PINNED_TIME
    )
    assert bar is not None
    assert bar.vwap == "3.5"


def test_aggregate_source_fact_range_tracks_which_facts() -> None:
    fact1 = _make_swap_fact(block_number=100, log_index=0, amount0_in="100", amount1_out="200")
    fact2 = _make_swap_fact(block_number=101, log_index=5, amount0_in="300", amount1_out="600")
    bar = aggregate_swaps_to_bar(
        [fact1, fact2], PAIR_ID, CHAIN_ID, BarInterval.ONE_MINUTE, PINNED_TIME, PINNED_TIME
    )
    assert bar is not None
    assert bar.source_fact_range == (fact1.fact_id, fact2.fact_id)
