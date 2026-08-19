"""Unit tests: collector + normalizer + fact_processor, driven by a fake
BlockchainProvider scripted with the REAL sample event captured from Base
block 13,500,004 during planning (Milestone1-ExecutionPlan § Open
Decisions).

Mocking boundary (DOC-013 § Testing Conventions): unit tests may mock at
the provider abstraction — never deeper. Determinism (DOC-013): the clock
is injected and pinned, so expected outputs are byte-exact constants.
"""

from datetime import UTC, datetime

import pytest

from onchain_platform.acquisition.collector import Collector, CollectedLog
from onchain_platform.acquisition.providers.base import (
    BlockMetadata,
    BlockchainProvider,
    RawLog,
)
from onchain_platform.domain.exceptions import AcquisitionError, DomainValidationError
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType
from onchain_platform.processing.fact_processor import FactProcessor
from onchain_platform.processing.normalizer import (
    PAIR_CREATED_TOPIC,
    normalize_pair_created,
)

BASE_CHAIN_ID = 8453
FACTORY = "0x8909dc15e40173ff4699343b6eb8132c65e18ec6"

# The real sample event, byte-for-byte as captured from eth_getLogs
# (block 13,500,004, tx 0xfc6bbb0b…, logIndex 0x2b = 43).
SAMPLE_LOG = RawLog(
    address=FACTORY,
    topics=(
        PAIR_CREATED_TOPIC,
        "0x0000000000000000000000004200000000000000000000000000000000000006",
        "0x00000000000000000000000084e42a7ce453f81d421587103af21c261f4d2a16",
    ),
    data=(
        "0x000000000000000000000000a431e9b572ca4a0ce1ba10812d3a7b1db718a957"
        "000000000000000000000000000000000000000000000000000000000001a0d3"
    ),
    block_number=13_500_004,
    block_hash="0xf7688420b215b621c41d64ec128184809fb3249bc1e70a07d8d197d94e821a41",
    transaction_hash="0xfc6bbb0b00fc647da45dd294ca6355f8f687f2c1ca132f0198d13f3796f54fbd",
    transaction_index=9,
    log_index=43,
    removed=False,
)
SAMPLE_BLOCK = BlockMetadata(
    number=13_500_004,
    hash="0xf7688420b215b621c41d64ec128184809fb3249bc1e70a07d8d197d94e821a41",
    timestamp=datetime.fromtimestamp(1_713_789_355, tz=UTC),  # 2024-04-22T12:35:55Z
)

# Pinned clock values — replay determinism (ADR-006 Principle 2).
PINNED_OBSERVED_AT = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)
PINNED_INGESTED_AT = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


class FakeProvider(BlockchainProvider):
    """Scripted provider: exact call sequences, no network."""

    def __init__(
        self,
        blocks: dict[int, BlockMetadata],
        logs_by_block: dict[int, list[RawLog]],
        head: int,
    ) -> None:
        self.blocks = blocks
        self.logs_by_block = logs_by_block
        self.head = head
        self.get_logs_calls: list[tuple[int, int]] = []
        self.get_block_calls: list[int] = []

    async def get_chain_id(self) -> int:
        return BASE_CHAIN_ID

    async def get_chain_head(self) -> int:
        return self.head

    async def get_block_metadata(self, block_number: int) -> BlockMetadata:
        self.get_block_calls.append(block_number)
        try:
            return self.blocks[block_number]
        except KeyError as exc:
            raise AcquisitionError(f"fake provider has no block {block_number}") from exc

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: list[str] | None = None,
    ) -> list[RawLog]:
        self.get_logs_calls.append((from_block, to_block))
        assert address == FACTORY
        assert topics == [PAIR_CREATED_TOPIC]
        result: list[RawLog] = []
        for bn in range(from_block, to_block + 1):
            result.extend(self.logs_by_block.get(bn, []))
        result.sort(key=lambda log: (log.block_number, log.log_index))
        return result

    async def close(self) -> None:
        return None


def make_collector(provider: BlockchainProvider, received: list[CollectedLog]) -> Collector:
    async def handler(collected: CollectedLog) -> None:
        received.append(collected)

    return Collector(
        provider,
        chain_id=BASE_CHAIN_ID,
        factory_address=FACTORY,
        event_topic=PAIR_CREATED_TOPIC,
        dex="uniswap_v2",
        handler=handler,
        clock=lambda: PINNED_OBSERVED_AT,
        poll_interval_seconds=0.0,
    )


async def test_collector_process_range_forwards_matching_logs_in_order() -> None:
    provider = FakeProvider(
        blocks={13_500_004: SAMPLE_BLOCK},
        logs_by_block={13_500_004: [SAMPLE_LOG]},
        head=13_500_004,
    )
    received: list[CollectedLog] = []
    collector = make_collector(provider, received)

    count = await collector.process_range(13_500_004, 13_500_004)

    assert count == 1
    assert len(received) == 1
    assert received[0].raw_log == SAMPLE_LOG
    assert received[0].block == SAMPLE_BLOCK
    # observed_at comes from the injected clock — never a wall-clock read
    # inside the Capability (DOC-013 § Determinism Discipline).
    assert received[0].observed_at == PINNED_OBSERVED_AT
    assert received[0].dex == "uniswap_v2"


async def test_collector_process_range_empty_blocks_forward_nothing() -> None:
    provider = FakeProvider(blocks={100: BlockMetadata(
        number=100, hash="0x" + "11" * 32, timestamp=datetime(2024, 1, 1, tzinfo=UTC)
    )}, logs_by_block={}, head=100)
    received: list[CollectedLog] = []
    collector = make_collector(provider, received)

    count = await collector.process_range(100, 100)
    assert count == 0
    assert received == []


async def test_collector_invalid_range_raises_acquisition_error() -> None:
    provider = FakeProvider(blocks={}, logs_by_block={}, head=0)
    collector = make_collector(provider, [])
    with pytest.raises(AcquisitionError):
        await collector.process_range(10, 5)


def test_normalizer_decodes_real_sample_log_byte_exactly() -> None:
    collected = CollectedLog(
        raw_log=SAMPLE_LOG,
        block=SAMPLE_BLOCK,
        observed_at=PINNED_OBSERVED_AT,
        dex="uniswap_v2",
    )
    normalized = normalize_pair_created(collected)

    # EIP-55 checksummed outputs (DOC-012 § Conventions).
    assert normalized.token0_address == "0x4200000000000000000000000000000000000006"
    assert normalized.token1_address == "0x84e42A7cE453F81d421587103AF21c261f4D2a16"
    assert normalized.pair_address == "0xa431E9B572CA4a0cE1BA10812d3a7B1DB718a957"
    assert normalized.block_number == 13_500_004
    assert normalized.tx_hash == SAMPLE_LOG.transaction_hash
    assert normalized.log_index == 43
    # event_time is the block timestamp — one canonical source (base.py).
    assert normalized.event_time == SAMPLE_BLOCK.timestamp
    assert normalized.dex == "uniswap_v2"


def test_normalizer_removed_log_raises_domain_validation_error() -> None:
    removed = SAMPLE_LOG.model_copy(update={"removed": True})
    collected = CollectedLog(
        raw_log=removed, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="removed"):
        normalize_pair_created(collected)


def test_normalizer_wrong_topic_raises_domain_validation_error() -> None:
    wrong = SAMPLE_LOG.model_copy(update={"topics": ("0x" + "aa" * 32,) + SAMPLE_LOG.topics[1:]})
    collected = CollectedLog(
        raw_log=wrong, block=SAMPLE_BLOCK, observed_at=PINNED_OBSERVED_AT, dex="uniswap_v2"
    )
    with pytest.raises(DomainValidationError, match="PairCreated signature"):
        normalize_pair_created(collected)


def test_fact_processor_produces_exact_pending_fact_for_real_sample() -> None:
    processor = FactProcessor(chain_id=BASE_CHAIN_ID, clock=lambda: PINNED_INGESTED_AT)
    collected = CollectedLog(
        raw_log=SAMPLE_LOG,
        block=SAMPLE_BLOCK,
        observed_at=PINNED_OBSERVED_AT,
        dex="uniswap_v2",
    )

    fact = processor.process(collected)

    # Every field byte-exact against the plan's captured constants —
    # this is the deterministic core Milestone 1 proves (ADR-006
    # Principle 2: identical inputs → identical outputs).
    assert fact.fact_id == "8453:0xfc6bbb0b00fc647da45dd294ca6355f8f687f2c1ca132f0198d13f3796f54fbd:43"
    assert fact.schema_version == "1.0"
    assert fact.chain_id == BASE_CHAIN_ID
    assert fact.fact_type == FactType.PAIR_CREATED
    assert fact.block_number == 13_500_004
    assert fact.block_hash == SAMPLE_BLOCK.hash
    assert fact.log_index == 43
    assert fact.event_time == SAMPLE_BLOCK.timestamp
    assert fact.observed_at == PINNED_OBSERVED_AT
    assert fact.ingested_at == PINNED_INGESTED_AT
    assert fact.confirmation_status == ConfirmationStatus.PENDING
    assert fact.confirmations == 0
    assert fact.payload.fact_type == "PAIR_CREATED"
    assert fact.payload.dex == "uniswap_v2"
    assert fact.payload.token0_address == "0x4200000000000000000000000000000000000006"


def test_fact_processor_non_pair_created_raises_domain_validation_error() -> None:
    processor = FactProcessor(chain_id=BASE_CHAIN_ID, clock=lambda: PINNED_INGESTED_AT)
    other_topic_log = SAMPLE_LOG.model_copy(
        update={"topics": ("0x" + "bb" * 32,) + SAMPLE_LOG.topics[1:]}
    )
    collected = CollectedLog(
        raw_log=other_topic_log,
        block=SAMPLE_BLOCK,
        observed_at=PINNED_OBSERVED_AT,
        dex="uniswap_v2",
    )
    with pytest.raises(DomainValidationError):
        processor.process(collected)
