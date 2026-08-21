"""Unit tests: liquidity normalizer + fact processor (Mint/Burn events).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions). All amount fields are str (zero-tolerance byte-identity
per DOC-008 § Financial Precision Principle).
"""

from datetime import UTC, datetime

import pytest

from onchain_platform.acquisition.collector import CollectedLog
from onchain_platform.acquisition.providers.base import BlockMetadata, RawLog
from onchain_platform.domain.exceptions import DomainValidationError
from onchain_platform.domain.schemas.blockchain_fact import (
    LiquidityAddedPayload,
    LiquidityRemovedPayload,
)
from onchain_platform.domain.schemas.enums import FactType
from onchain_platform.processing.fact_processor import FactProcessor
from onchain_platform.processing.normalizer import (
    BURN_TOPIC,
    MINT_TOPIC,
    normalize_liquidity,
)

CHAIN_ID = 8453
PINNED_OBSERVED_AT = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
PINNED_INGESTED_AT = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
POOL_ADDRESS = "0x39f0E675D479088DE08b7f201Ac08e20F899B838"
SENDER = "0xeef9027F3b887713D91C4C0965a08d1776859b00"

SAMPLE_MINT_LOG = RawLog(
    address=POOL_ADDRESS,
    topics=(
        MINT_TOPIC,
        "0x000000000000000000000000eef9027f3b887713d91c4c0965a08d1776859b00",
    ),
    data=(
        "0x"
        "00000000000000000000000000000000000000000000000000000000000003e8"
        "00000000000000000000000000000000000000000000000000000000000007d0"
    ),
    block_number=13_500_100,
    block_hash="0x" + "aa" * 32,
    transaction_hash="0x" + "bb" * 32,
    transaction_index=0,
    log_index=5,
    removed=False,
)
SAMPLE_BLOCK = BlockMetadata(
    number=13_500_100,
    hash="0x" + "aa" * 32,
    parent_hash="0x" + "99" * 32,
    timestamp=datetime(2024, 4, 22, 13, 0, 0, tzinfo=UTC),
)


def test_mint_normalizer_decodes_correctly() -> None:
    collected = CollectedLog(
        raw_log=SAMPLE_MINT_LOG,
        block=SAMPLE_BLOCK,
        observed_at=PINNED_OBSERVED_AT,
        dex="uniswap_v2",
    )
    normalized = normalize_liquidity(collected)

    from eth_utils.address import to_checksum_address

    assert normalized.pool_address == to_checksum_address(POOL_ADDRESS.lower())
    assert normalized.provider == to_checksum_address(SENDER.lower())
    # amount0=1000 (0x3e8), amount1=2000 (0x7d0)
    assert normalized.amount0 == "1000"
    assert normalized.amount1 == "2000"
    assert normalized.block_number == 13_500_100


def test_burn_normalizer_decodes_correctly() -> None:
    burn_log = SAMPLE_MINT_LOG.model_copy(
        update={
            "topics": (
                BURN_TOPIC,
                SAMPLE_MINT_LOG.topics[1],
            )
        }
    )
    collected = CollectedLog(
        raw_log=burn_log,
        block=SAMPLE_BLOCK,
        observed_at=PINNED_OBSERVED_AT,
        dex="uniswap_v2",
    )
    normalized = normalize_liquidity(collected)
    assert normalized.amount0 == "1000"
    assert normalized.amount1 == "2000"


def test_liquidity_normalizer_rejected_removed_log() -> None:
    removed = SAMPLE_MINT_LOG.model_copy(update={"removed": True})
    collected = CollectedLog(
        raw_log=removed, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="removed"):
        normalize_liquidity(collected)


def test_liquidity_normalizer_rejected_wrong_topic() -> None:
    wrong = SAMPLE_MINT_LOG.model_copy(
        update={"topics": ("0x" + "ff" * 32,) + SAMPLE_MINT_LOG.topics[1:]}
    )
    collected = CollectedLog(
        raw_log=wrong, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="not a V2 Mint or Burn"):
        normalize_liquidity(collected)


def test_liquidity_normalizer_rejected_zero_amounts() -> None:
    zero_data = "0x" + "00" * 64
    zero_log = SAMPLE_MINT_LOG.model_copy(update={"data": zero_data})
    collected = CollectedLog(
        raw_log=zero_log, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="positive"):
        normalize_liquidity(collected)


def test_fact_processor_mint_produces_liquidity_added() -> None:
    processor = FactProcessor(chain_id=CHAIN_ID, clock=lambda: PINNED_INGESTED_AT)
    collected = CollectedLog(
        raw_log=SAMPLE_MINT_LOG,
        block=SAMPLE_BLOCK,
        observed_at=PINNED_OBSERVED_AT,
        dex="uniswap_v2",
    )
    fact = processor.process(collected)

    assert fact.fact_type == FactType.LIQUIDITY_ADDED
    assert fact.confirmation_status.value == "PENDING"
    payload = fact.payload
    assert isinstance(payload, LiquidityAddedPayload)
    assert payload.amount0 == "1000"
    assert payload.amount1 == "2000"
    assert payload.fact_type == "LIQUIDITY_ADDED"


def test_fact_processor_burn_produces_liquidity_removed() -> None:
    burn_log = SAMPLE_MINT_LOG.model_copy(
        update={
            "topics": (
                BURN_TOPIC,
                SAMPLE_MINT_LOG.topics[1],
            )
        }
    )
    processor = FactProcessor(chain_id=CHAIN_ID, clock=lambda: PINNED_INGESTED_AT)
    collected = CollectedLog(
        raw_log=burn_log,
        block=SAMPLE_BLOCK,
        observed_at=PINNED_OBSERVED_AT,
        dex="uniswap_v2",
    )
    fact = processor.process(collected)

    assert fact.fact_type == FactType.LIQUIDITY_REMOVED
    payload = fact.payload
    assert isinstance(payload, LiquidityRemovedPayload)
    assert payload.amount0 == "1000"
    assert payload.fact_type == "LIQUIDITY_REMOVED"
