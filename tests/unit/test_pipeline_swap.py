"""Unit tests: Swap normalizer + fact processor, driven by a REAL captured
Swap log from Base block 13,500,004, logIndex 22.

DOC-013 § Testing Conventions: naming test_<unit>_<scenario>_<
expected_outcome>. All amount fields are str (zero-tolerance byte-identity
per DOC-008 § Financial Precision Principle).
"""

from datetime import UTC, datetime

import pytest

from onchain_platform.acquisition.collector import CollectedLog
from onchain_platform.acquisition.providers.base import BlockMetadata, RawLog
from onchain_platform.domain.exceptions import DomainValidationError
from onchain_platform.domain.schemas.blockchain_fact import SwapExecutedPayload
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType
from onchain_platform.processing.fact_processor import FactProcessor
from onchain_platform.processing.normalizer import (
    SWAP_TOPIC,
    normalize_swap,
)

CHAIN_ID = 8453
PINNED_OBSERVED_AT = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)
PINNED_INGESTED_AT = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)

# Real captured Swap log from Base block 13,500,004, logIndex 22.
# Pool: 0x39f0e675d479088de08b7f201ac08e20f899b838
# amount0_in=0, amount1_in=34099401194346, amount0_out=14339668586465206, amount1_out=0
SAMPLE_SWAP_LOG = RawLog(
    address="0x39f0e675d479088de08b7f201ac08e20f899b838",
    topics=(
        SWAP_TOPIC,
        "0x000000000000000000000000eef9027f3b887713d91c4c0965a08d1776859b00",
        "0x000000000000000000000000eef9027f3b887713d91c4c0965a08d1776859b00",
    ),
    data=(
        "0x"
        "0000000000000000000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000000000001f0362b1fb6a"
        "0000000000000000000000000000000000000000000000000032f1da444b07b6"
        "0000000000000000000000000000000000000000000000000000000000000000"
    ),
    block_number=13_500_004,
    block_hash="0xf7688420b215b621c41d64ec128184809fb3249bc1e70a07d8d197d94e821a41",
    transaction_hash="0xd56d17897ef366e2" + "00" * 24,  # truncated for fixture
    transaction_index=9,
    log_index=22,
    removed=False,
)
SAMPLE_BLOCK = BlockMetadata(
    number=13_500_004,
    hash="0xf7688420b215b621c41d64ec128184809fb3249bc1e70a07d8d197d94e821a41",
    parent_hash="0x6b628a4744f41af5c3ba80d4bc898421c074a751dc9f91a71325812d11d36dcd",
    timestamp=datetime.fromtimestamp(1_713_789_355, tz=UTC),  # 2024-04-22T12:35:55Z
)


def test_swap_normalizer_decodes_real_log_byte_exactly() -> None:
    collected = CollectedLog(
        raw_log=SAMPLE_SWAP_LOG,
        block=SAMPLE_BLOCK,
        observed_at=PINNED_OBSERVED_AT,
        dex="uniswap_v2",
    )
    normalized = normalize_swap(collected)

    # Pool address is the emitting contract, EIP-55 checksummed.
    from eth_utils.address import to_checksum_address

    expected_pool = to_checksum_address("0x39f0e675d479088de08b7f201ac08e20f899b838")
    assert normalized.pool_address == expected_pool

    # Sender and recipient are checksummed.
    expected_addr = to_checksum_address("0xeef9027f3b887713d91c4c0965a08d1776859b00")
    assert normalized.sender == expected_addr
    assert normalized.recipient == expected_addr

    # Amounts are decimal strings — zero-tolerance byte-identity (DOC-008 §
    # Financial Precision Principle).
    assert normalized.amount0_in == "0"
    assert normalized.amount1_in == "34099401194346"
    assert normalized.amount0_out == "14339668586465206"
    assert normalized.amount1_out == "0"

    assert normalized.block_number == 13_500_004
    assert normalized.log_index == 22
    assert normalized.event_time == SAMPLE_BLOCK.timestamp
    assert normalized.dex == "uniswap_v2"


def test_swap_normalizer_rejected_removed_log() -> None:
    removed = SAMPLE_SWAP_LOG.model_copy(update={"removed": True})
    collected = CollectedLog(
        raw_log=removed, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="removed"):
        normalize_swap(collected)


def test_swap_normalizer_rejected_wrong_topic() -> None:
    wrong = SAMPLE_SWAP_LOG.model_copy(
        update={"topics": ("0x" + "aa" * 32,) + SAMPLE_SWAP_LOG.topics[1:]}
    )
    collected = CollectedLog(
        raw_log=wrong, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="V2 Swap signature"):
        normalize_swap(collected)


def test_swap_normalizer_rejected_all_zeros() -> None:
    """A swap with all amounts zero is nonsensical."""
    zero_data = "0x" + "00" * 128
    zero_log = SAMPLE_SWAP_LOG.model_copy(update={"data": zero_data})
    collected = CollectedLog(
        raw_log=zero_log, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="both amount0_in=0 and amount1_in=0"):
        normalize_swap(collected)


def test_swap_normalizer_rejected_both_ins_nonzero() -> None:
    """V2 swaps are one-directional: exactly one _in field is > 0."""
    data = (
        "0x"
        "0000000000000000000000000000000000000000000000000000000000000001"  # amount0_in = 1
        "0000000000000000000000000000000000000000000000000000000000000002"  # amount1_in = 2
        "0000000000000000000000000000000000000000000000000000000000000003"  # amount0_out = 3
        "0000000000000000000000000000000000000000000000000000000000000000"  # amount1_out = 0
    )
    bad_log = SAMPLE_SWAP_LOG.model_copy(update={"data": data})
    collected = CollectedLog(
        raw_log=bad_log, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="both amount0_in=1 and amount1_in=2"):
        normalize_swap(collected)


def test_fact_processor_produces_exact_pending_swap_fact() -> None:
    processor = FactProcessor(chain_id=CHAIN_ID, clock=lambda: PINNED_INGESTED_AT)
    collected = CollectedLog(
        raw_log=SAMPLE_SWAP_LOG,
        block=SAMPLE_BLOCK,
        observed_at=PINNED_OBSERVED_AT,
        dex="uniswap_v2",
    )

    fact = processor.process(collected)

    assert fact.fact_type == FactType.SWAP_EXECUTED
    assert fact.confirmation_status == ConfirmationStatus.PENDING
    assert fact.confirmations == 0
    assert fact.schema_version == "1.0"
    assert fact.chain_id == CHAIN_ID
    assert fact.block_number == 13_500_004
    assert fact.log_index == 22
    assert fact.event_time == SAMPLE_BLOCK.timestamp
    assert fact.observed_at == PINNED_OBSERVED_AT
    assert fact.ingested_at == PINNED_INGESTED_AT

    payload = fact.payload
    assert isinstance(payload, SwapExecutedPayload)
    assert payload.fact_type == "SWAP_EXECUTED"
    assert payload.amount0_in == "0"
    assert payload.amount1_in == "34099401194346"
    assert payload.amount0_out == "14339668586465206"
    assert payload.amount1_out == "0"


def test_fact_processor_rejected_unknown_topic() -> None:
    processor = FactProcessor(chain_id=CHAIN_ID, clock=lambda: PINNED_INGESTED_AT)
    unknown_log = SAMPLE_SWAP_LOG.model_copy(
        update={"topics": ("0x" + "ff" * 32,) + SAMPLE_SWAP_LOG.topics[1:]}
    )
    collected = CollectedLog(
        raw_log=unknown_log, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="unknown topic0"):
        processor.process(collected)
