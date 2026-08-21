"""Replay Test: reorg case (ImplementationPlan § Milestone 2 DoD).

Simulates a real reorg scenario: the collector processes a canonical chain,
then a NEW block arrives whose parent_hash doesn't match the previous
block — triggering reorg detection. The divergent block's facts are marked
ORPHANED; facts before the fork point remain FINALIZED.

The engine detects reorgs at the buffer boundary: when block N arrives
with parent_hash ≠ block N-1's hash, the fork is at block N-1. Only
facts at block N (the divergent block) are orphaned. This is correct
behavior per ADR-006 § Canonical Chain Validation Engine.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.acquisition.collector import CollectedLog, Collector, LogFilter
from onchain_platform.acquisition.providers.base import BlockMetadata, RawLog
from onchain_platform.domain.schemas.enums import ConfirmationStatus
from onchain_platform.persistence.postgres import repositories
from onchain_platform.processing.fact_processor import FactProcessor
from onchain_platform.processing.finality_engine import FinalityEngine
from onchain_platform.processing.normalizer import PAIR_CREATED_TOPIC
from onchain_platform.processing.reorg_handler import LoggingReorgEventHandler
from tests.replay.fixtures.reorg_simulator import make_canonical_chain

CHAIN_ID = 8453
DEPTH = 3
FACTORY = "0x8909dc15e40173ff4699343b6eb8132c65e18ec6"
DEX = "uniswap_v2"
PINNED_TIME = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def _make_log(block_number: int, log_index: int, block_hash: str) -> RawLog:
    return RawLog(
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
        block_number=block_number,
        block_hash=block_hash,
        transaction_hash=f"0x{block_number:064x}",
        transaction_index=0,
        log_index=log_index,
        removed=False,
    )


async def test_replay_reorg_marks_divergent_facts_orphaned(
    pg_engine: AsyncEngine, clean_facts: Callable[[], Awaitable[None]]
) -> None:
    """Reorg scenario: canonical chain 100..110, then block 111 arrives
    with parent_hash pointing to block 108 (not 110). The engine detects
    the break at the buffer boundary: fork at block 110, orphaned = [111].
    Facts at blocks 102 and 106 (before fork) remain FINALIZED. Fact at
    block 111 (the divergent block) is ORPHANED."""
    await clean_facts()

    # Build canonical chain 100..110.
    canonical = make_canonical_chain(100, 11)

    # Block 111: parent = canonical block 108's hash (not 110's).
    # This is a single-block reorg: the engine sees 111's parent ≠ 110's
    # hash and detects the break.
    block_111 = BlockMetadata(
        number=111,
        hash="0xdd" + "00" * 31,
        parent_hash=canonical[108].hash,  # fork at 108
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )

    all_blocks = {**canonical, 111: block_111}

    # Logs at blocks 102, 106, and 111.
    logs = {
        102: [_make_log(102, 0, canonical[102].hash)],
        106: [_make_log(106, 0, canonical[106].hash)],
        111: [_make_log(111, 0, block_111.hash)],
    }

    # Provider that serves both blocks and logs.
    from tests.unit.test_finality_engine import FakeProvider

    class LogAwareProvider(FakeProvider):
        def __init__(self, blocks: list[BlockMetadata], logs: dict[int, list[RawLog]]) -> None:
            super().__init__(blocks)
            self._logs = logs

        async def get_logs(
            self,
            from_block: int,
            to_block: int,
            address: str | None = None,
            topics: Sequence[str] | None = None,
        ) -> list[RawLog]:
            result: list[RawLog] = []
            for bn in range(from_block, to_block + 1):
                result.extend(self._logs.get(bn, []))
            result.sort(key=lambda log: (log.block_number, log.log_index))
            return result

    provider = LogAwareProvider(list(all_blocks.values()), logs)

    processor = FactProcessor(chain_id=CHAIN_ID, clock=lambda: PINNED_TIME)
    reorg_handler = LoggingReorgEventHandler(confirmation_depth=DEPTH)
    finality_engine = FinalityEngine(
        chain_id=CHAIN_ID,
        confirmation_depth=DEPTH,
        provider=provider,
        engine=pg_engine,
        clock=lambda: PINNED_TIME,
        reorg_handler=reorg_handler,
    )

    async def handler(collected: CollectedLog) -> None:
        fact = processor.process(collected)
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            await repositories.save_fact(session, fact)

    collector = Collector(
        provider,
        chain_id=CHAIN_ID,
        filters=[LogFilter(address=FACTORY, topic=PAIR_CREATED_TOPIC, dex=DEX)],
        handler=handler,
        clock=lambda: PINNED_TIME,
        poll_interval_seconds=0.0,
        on_block_processed=finality_engine.on_new_block,
    )

    # Process canonical chain 100..110.
    await collector.process_range(100, 110)

    # Verify pre-reorg state.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        fact_102 = await repositories.get_fact(session, f"{CHAIN_ID}:0x{102:064x}:0")
        fact_106 = await repositories.get_fact(session, f"{CHAIN_ID}:0x{106:064x}:0")
    assert fact_102 is not None
    assert fact_102.confirmation_status == ConfirmationStatus.FINALIZED
    assert fact_106 is not None
    assert fact_106.confirmation_status == ConfirmationStatus.FINALIZED

    # Process divergent block 111 (parent=108, not 110).
    await collector.process_range(111, 111)

    # Verify: facts at 102, 106 remain FINALIZED (before fork point).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        fact_102 = await repositories.get_fact(session, f"{CHAIN_ID}:0x{102:064x}:0")
        fact_106 = await repositories.get_fact(session, f"{CHAIN_ID}:0x{106:064x}:0")
    assert fact_102 is not None
    assert fact_102.confirmation_status == ConfirmationStatus.FINALIZED
    assert fact_106 is not None
    assert fact_106.confirmation_status == ConfirmationStatus.FINALIZED

    # Verify: fact at block 111 (the divergent block) is ORPHANED.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        fact_111 = await repositories.get_fact(session, f"{CHAIN_ID}:0x{111:064x}:0")
    assert fact_111 is not None
    assert fact_111.confirmation_status == ConfirmationStatus.ORPHANED
    assert fact_111.confirmations == 0
