"""FIRST REPLAY TEST — Milestone 1 (ImplementationPlan § Milestone 1 DoD).

Re-processes the committed fixture (Base blocks 13,500,000–13,500,024,
5 real PairCreated events) through the LIVE pipeline — collector →
normalizer → fact processor — and asserts:

1. byte-identical output against frozen, independently-derived expected
   values (every field str/int/enum → zero tolerance; DOC-010 § Replay
   Tests, DOC-013 § Determinism Discipline: byte-identity is reserved for
   Decimal/String/structural fields — M1 has no float field, so all fields
   qualify);
2. a second run of the same pipeline produces the SAME facts, byte for
   byte (ADR-006 Principle 2 — reproducibility proven, not assumed);
3. the facts land in REAL Postgres and read back identical, with no
   duplicates on re-run (ADR-006 § Idempotency).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.acquisition.collector import CollectedLog, Collector, LogFilter
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    PairCreatedPayload,
)
from onchain_platform.persistence.postgres import repositories
from onchain_platform.processing.fact_processor import FactProcessor
from tests.replay.fixture_provider import FIXTURES_DIR, FixtureProvider

FIXTURE_PATH = FIXTURES_DIR / "base_pair_created_13500000_13500024.json"
EXPECTED_EVENT_COUNT = 5
CHAIN_ID = 8453
DEX = "uniswap_v2"

# Frozen expected values for the sample event at block 13,500,004 — derived
# independently during planning from the raw captured eth_getLogs entry
# (Milestone1-ExecutionPlan § Open Decisions), NOT from running the
# pipeline. Checksum forms verified directly against eth_utils.
SAMPLE_FACT_ID = "8453:0xfc6bbb0b00fc647da45dd294ca6355f8f687f2c1ca132f0198d13f3796f54fbd:43"
SAMPLE_TX_HASH = "0xfc6bbb0b00fc647da45dd294ca6355f8f687f2c1ca132f0198d13f3796f54fbd"
SAMPLE_BLOCK_HASH = "0xf7688420b215b621c41d64ec128184809fb3249bc1e70a07d8d197d94e821a41"
SAMPLE_EVENT_TIME = datetime(2024, 4, 22, 12, 35, 55, tzinfo=UTC)
SAMPLE_TOKEN0 = "0x4200000000000000000000000000000000000006"  # WETH (Base)
SAMPLE_TOKEN1 = "0x84e42A7cE453F81d421587103AF21c261f4D2a16"
SAMPLE_PAIR = "0xa431E9B572CA4a0cE1BA10812d3a7B1DB718a957"

# Canonical order (block_number ascending, then log_index) of the fixture's
# five events — read from the committed fixture file itself during planning.
EXPECTED_BLOCKS = [13_500_004, 13_500_010, 13_500_017, 13_500_020, 13_500_022]
EXPECTED_LOG_INDICES = [43, 130, 205, 252, 188]


def _load_fixture() -> FixtureProvider:
    return FixtureProvider(FIXTURE_PATH)


async def _run_pipeline(provider: FixtureProvider) -> list[BlockchainFact]:
    """One full pass: collector → normalizer → fact processor (the live
    path — ADR-006 § Single Processing Path). The injected clocks are
    pinned to the fixture's own constants: replay determinism (ADR-006
    Principle 2; DOC-013 § Determinism Discipline — no wall-clock reads in
    Capabilities)."""
    processor = FactProcessor(chain_id=provider.chain_id, clock=lambda: provider.ingested_at)
    facts: list[BlockchainFact] = []

    async def handler(collected: CollectedLog) -> None:
        facts.append(processor.process(collected))

    collector = Collector(
        provider,
        chain_id=provider.chain_id,
        filters=[LogFilter(address=provider.factory_address, topic=provider.event_topic, dex=DEX)],
        handler=handler,
        clock=lambda: provider.observed_at,
        poll_interval_seconds=0.0,
    )
    count = await collector.process_range(provider.from_block, provider.to_block)
    assert count == len(facts) == EXPECTED_EVENT_COUNT
    return facts


async def test_replay_pair_created_produces_expected_facts_byte_identical() -> None:
    provider = _load_fixture()
    facts = await _run_pipeline(provider)

    # Structural presence: exactly the 5 fixture events, canonical order.
    assert [f.block_number for f in facts] == EXPECTED_BLOCKS
    assert [f.log_index for f in facts] == EXPECTED_LOG_INDICES

    sample = facts[0]
    # Byte-identical, zero tolerance — every one of these fields is str/int/
    # enum (DOC-010 § Replay Tests: Decimal/String fields are zero-tolerance).
    assert sample.fact_id == SAMPLE_FACT_ID
    assert sample.schema_version == "1.0"
    assert sample.chain_id == CHAIN_ID
    assert sample.fact_type.value == "PAIR_CREATED"
    assert sample.block_number == 13_500_004
    assert sample.block_hash == SAMPLE_BLOCK_HASH
    assert sample.tx_hash == SAMPLE_TX_HASH
    assert sample.log_index == 43
    assert sample.event_time == SAMPLE_EVENT_TIME
    assert sample.observed_at == provider.observed_at
    assert sample.ingested_at == provider.ingested_at
    assert sample.confirmation_status.value == "PENDING"
    assert sample.confirmations == 0
    payload = sample.payload
    assert isinstance(payload, PairCreatedPayload)
    assert payload.pair_address == SAMPLE_PAIR
    assert payload.token0_address == SAMPLE_TOKEN0
    assert payload.token1_address == SAMPLE_TOKEN1
    assert payload.dex == DEX


async def test_replay_second_run_is_byte_identical() -> None:
    # ADR-006 Principle 2: given identical blockchain history, the platform
    # always produces identical Facts — proven by running the pipeline twice
    # (fresh provider instances) and comparing serialized bytes.
    facts_a = await _run_pipeline(_load_fixture())
    facts_b = await _run_pipeline(_load_fixture())

    serialized_a = [f.model_dump_json() for f in facts_a]
    serialized_b = [f.model_dump_json() for f in facts_b]
    assert serialized_a == serialized_b


async def test_replay_into_real_postgres_is_idempotent(
    pg_engine: AsyncEngine, clean_facts: Callable[[], Awaitable[None]]
) -> None:
    # DoD: same block range twice → same rows, no duplicates (ADR-006 §
    # Idempotency), against REAL Postgres (DOC-010 § Integration Tests).
    await clean_facts()
    provider = _load_fixture()
    processor = FactProcessor(chain_id=provider.chain_id, clock=lambda: provider.ingested_at)

    async def handler(collected: CollectedLog) -> None:
        fact = processor.process(collected)
        # Session scoped to this call, never stored (DOC-013 § Async
        # Conventions).
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            await repositories.save_fact(session, fact)

    collector = Collector(
        provider,
        chain_id=provider.chain_id,
        filters=[LogFilter(address=provider.factory_address, topic=provider.event_topic, dex=DEX)],
        handler=handler,
        clock=lambda: provider.observed_at,
        poll_interval_seconds=0.0,
    )

    # Pass 1.
    await collector.process_range(provider.from_block, provider.to_block)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        count_after_first = await repositories.count_facts_for_chain(session, CHAIN_ID)
        rows_first = await repositories.list_facts_for_chain(session, CHAIN_ID)
    assert count_after_first == EXPECTED_EVENT_COUNT

    # Pass 2 — same range, same DB: idempotent, no duplicates.
    await collector.process_range(provider.from_block, provider.to_block)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        count_after_second = await repositories.count_facts_for_chain(session, CHAIN_ID)
        rows_second = await repositories.list_facts_for_chain(session, CHAIN_ID)
    assert count_after_second == EXPECTED_EVENT_COUNT

    # Rows read back byte-identical to what pass 1 stored (and both passes
    # produce the same row set).
    first_dump = [f.model_dump(mode="json") for f in rows_first]
    second_dump = [f.model_dump(mode="json") for f in rows_second]
    assert first_dump == second_dump

    # The sample row matches the frozen expected values end to end — the
    # complete vertical slice: real chain data → real row, correct in every
    # field.
    sample = next(f for f in rows_second if f.fact_id == SAMPLE_FACT_ID)
    assert sample.block_hash == SAMPLE_BLOCK_HASH
    assert sample.event_time == SAMPLE_EVENT_TIME
    sample_payload = sample.payload
    assert isinstance(sample_payload, PairCreatedPayload)
    assert sample_payload.token0_address == SAMPLE_TOKEN0
    assert sample_payload.pair_address == SAMPLE_PAIR
