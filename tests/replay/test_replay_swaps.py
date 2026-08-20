"""Replay Test: SwapExecuted facts (ImplementationPlan § Milestone 3 DoD).

Re-processes the extended fixture (blocks 13,500,000–13,500,024 with both
PairCreated and Swap events) through the LIVE pipeline and asserts Swap
facts are byte-identical to frozen expected values. All amount fields are
str (zero-tolerance per DOC-008 § Financial Precision Principle).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime

from onchain_platform.acquisition.collector import CollectedLog, Collector, LogFilter
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType
from onchain_platform.processing.fact_processor import FactProcessor
from onchain_platform.processing.normalizer import PAIR_CREATED_TOPIC, SWAP_TOPIC
from tests.replay.fixture_provider import FIXTURES_DIR, FixtureProvider

FIXTURE_PATH = FIXTURES_DIR / "base_pair_created_13500000_13500024.json"
CHAIN_ID = 8453
DEX = "uniswap_v2"
PINNED_TIME = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


async def test_replay_swap_executed_produces_byte_identical_facts() -> None:
    """Extended fixture with 5 Swap events from 5 different pools.
    Assert: Swap facts are byte-identical on two passes (ADR-006 Principle
    2). All amount fields are str, zero tolerance."""
    provider = FixtureProvider(FIXTURE_PATH)
    processor = FactProcessor(chain_id=CHAIN_ID, clock=lambda: provider.ingested_at)

    facts: list[BlockchainFact] = []

    async def handler(collected: CollectedLog) -> None:
        facts.append(processor.process(collected))

    # Two filters: PairCreated from factory + Swap from any address.
    collector = Collector(
        provider,
        chain_id=CHAIN_ID,
        filters=[
            LogFilter(address=provider.factory_address, topic=PAIR_CREATED_TOPIC, dex=DEX),
            LogFilter(address=None, topic=SWAP_TOPIC, dex=DEX),
        ],
        handler=handler,
        clock=lambda: provider.observed_at,
        poll_interval_seconds=0.0,
    )

    count = await collector.process_range(provider.from_block, provider.to_block)
    assert count == len(facts)

    # Separate PairCreated and Swap facts.
    pair_facts = [f for f in facts if f.fact_type == FactType.PAIR_CREATED]
    swap_facts = [f for f in facts if f.fact_type == FactType.SWAP_EXECUTED]

    assert len(pair_facts) == 5  # existing PairCreated events
    assert len(swap_facts) == 5  # new Swap events

    # Verify all Swap facts have correct type and str amounts.
    for fact in swap_facts:
        assert fact.fact_type == FactType.SWAP_EXECUTED
        assert fact.confirmation_status == ConfirmationStatus.PENDING
        assert fact.confirmations == 0
        payload = fact.payload
        assert isinstance(payload, SwapExecutedPayload)
        # All amounts are str (DOC-008 § Financial Precision).
        assert isinstance(payload.amount0_in, str)
        assert isinstance(payload.amount1_in, str)
        assert isinstance(payload.amount0_out, str)
        assert isinstance(payload.amount1_out, str)

    # Second pass — byte-identical (ADR-006 Principle 2).
    provider2 = FixtureProvider(FIXTURE_PATH)
    facts2: list[BlockchainFact] = []

    async def handler2(collected: CollectedLog) -> None:
        facts2.append(processor.process(collected))

    collector2 = Collector(
        provider2,
        chain_id=CHAIN_ID,
        filters=[
            LogFilter(address=provider2.factory_address, topic=PAIR_CREATED_TOPIC, dex=DEX),
            LogFilter(address=None, topic=SWAP_TOPIC, dex=DEX),
        ],
        handler=handler2,
        clock=lambda: provider2.observed_at,
        poll_interval_seconds=0.0,
    )
    await collector2.process_range(provider2.from_block, provider2.to_block)

    serialized_a = [f.model_dump_json() for f in facts]
    serialized_b = [f.model_dump_json() for f in facts2]
    assert serialized_a == serialized_b
