"""Integration tests: reorg handling against real Postgres.

Tests run against real infrastructure, never mocks (DOC-010 § Integration
Tests, DOC-011 § tests). Naming: test_<unit>_<scenario>_<expected_outcome>
(DOC-013 § Testing Conventions).
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.acquisition.providers.base import (
    BlockchainProvider,
    BlockMetadata,
    RawLog,
)
from onchain_platform.domain.schemas.enums import ConfirmationStatus
from onchain_platform.persistence.postgres import repositories
from onchain_platform.processing.finality_engine import FinalityEngine
from onchain_platform.processing.reorg_handler import LoggingReorgEventHandler
from tests.factories.blockchain_fact import blockchain_fact

CHAIN_ID = 8453
DEPTH = 3
FACTORY = "0x8909dc15e40173ff4699343b6eb8132c65e18ec6"
DEX = "uniswap_v2"
PINNED_TIME = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def _make_block(number: int, parent_hash: str = "", hash_: str = "") -> BlockMetadata:
    return BlockMetadata(
        number=number,
        hash=hash_ or f"0x{number:064x}",
        parent_hash=parent_hash or f"0x{number - 1:064x}",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _make_chain(start: int, count: int) -> list[BlockMetadata]:
    blocks = []
    for i in range(count):
        num = start + i
        blocks.append(_make_block(num, parent_hash=f"0x{num - 1:064x}", hash_=f"0x{num:064x}"))
    return blocks


class FakeProvider(BlockchainProvider):
    def __init__(self, blocks: list[BlockMetadata]) -> None:
        self._blocks = {b.number: b for b in blocks}

    async def get_chain_id(self) -> int:
        return CHAIN_ID

    async def get_chain_head(self) -> int:
        return max(self._blocks.keys())

    async def get_block_metadata(self, block_number: int) -> BlockMetadata:
        return self._blocks[block_number]

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> list[RawLog]:
        return []

    async def close(self) -> None:
        return None


async def test_reorg_marks_orphaned_not_finalized(
    pg_engine: AsyncEngine, clean_facts: Callable[[], Awaitable[None]]
) -> None:
    """Simulated reorg: canonical chain 100..110, then block 111 arrives
    with parent=108 (not 110). Facts before fork remain FINALIZED;
    divergent block's fact is ORPHANED. Checkpoint does not advance past
    fork point."""
    await clean_facts()

    canonical = _make_chain(100, 11)  # 100..110
    block_111 = _make_block(111, parent_hash=f"0x{108:064x}", hash_="0xdd" + "00" * 31)
    all_blocks = canonical + [block_111]
    provider = FakeProvider(all_blocks)

    # Insert a fact at block 102 (will be FINALIZED before reorg).
    fact_102 = blockchain_fact(block_number=102, chain_id=CHAIN_ID)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.save_fact(session, fact_102)

    # Insert a fact at block 111 (will be ORPHANED after reorg).
    fact_111 = blockchain_fact(
        block_number=111,
        chain_id=CHAIN_ID,
        tx_hash=f"0x{111:064x}",
        log_index=0,
    )
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.save_fact(session, fact_111)

    reorg_handler = LoggingReorgEventHandler(confirmation_depth=DEPTH)
    finality_engine = FinalityEngine(
        chain_id=CHAIN_ID,
        confirmation_depth=DEPTH,
        provider=provider,
        engine=pg_engine,
        clock=lambda: PINNED_TIME,
        reorg_handler=reorg_handler,
    )

    # Process canonical chain 100..110.
    for bn in range(100, 111):
        await finality_engine.on_new_block(bn)

    # Verify: fact_102 is FINALIZED (110-102=8 >= depth 3).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        row = await repositories.get_fact(session, fact_102.fact_id)
    assert row is not None
    assert row.confirmation_status == ConfirmationStatus.FINALIZED

    # Verify: checkpoint advanced to 110-3=107.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        cp = await repositories.get_checkpoint(session, CHAIN_ID)
    assert cp is not None
    assert cp.last_finalized_block == 107

    # Process divergent block 111 (parent=108, not 110).
    await finality_engine.on_new_block(111)

    # Verify: fact_102 remains FINALIZED.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        row = await repositories.get_fact(session, fact_102.fact_id)
    assert row is not None
    assert row.confirmation_status == ConfirmationStatus.FINALIZED

    # Verify: fact_111 is ORPHANED.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        row = await repositories.get_fact(session, fact_111.fact_id)
    assert row is not None
    assert row.confirmation_status == ConfirmationStatus.ORPHANED
    assert row.confirmations == 0

    # Verify: checkpoint did NOT advance past 107 (reorg doesn't advance).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        cp = await repositories.get_checkpoint(session, CHAIN_ID)
    assert cp is not None
    assert cp.last_finalized_block == 107


async def test_checkpoint_recovery_after_restart(
    pg_engine: AsyncEngine, clean_facts: Callable[[], Awaitable[None]]
) -> None:
    """Kill mid-block, restart: no duplicate facts, no lost pending facts,
    checkpoint correctly resumes."""
    await clean_facts()

    blocks = _make_chain(100, 20)  # 100..119
    provider = FakeProvider(blocks)

    # Insert facts at blocks 100, 105, 110.
    for bn in [100, 105, 110]:
        fact = blockchain_fact(
            block_number=bn,
            chain_id=CHAIN_ID,
            tx_hash=f"0x{bn:064x}",
            log_index=bn,
        )
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            await repositories.save_fact(session, fact)

    reorg_handler = LoggingReorgEventHandler(confirmation_depth=DEPTH)

    # "Pass 1": process blocks 100..110 (simulating a run that gets killed
    # after block 110).
    engine1 = FinalityEngine(
        chain_id=CHAIN_ID,
        confirmation_depth=DEPTH,
        provider=provider,
        engine=pg_engine,
        clock=lambda: PINNED_TIME,
        reorg_handler=reorg_handler,
    )
    for bn in range(100, 111):
        await engine1.on_new_block(bn)

    # Verify checkpoint after pass 1.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        cp = await repositories.get_checkpoint(session, CHAIN_ID)
    assert cp is not None
    assert cp.last_finalized_block == 107  # 110 - 3

    # Verify: fact at 100 is FINALIZED.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        row = await repositories.get_fact(session, f"{CHAIN_ID}:0x{100:064x}:{100}")
    assert row is not None
    assert row.confirmation_status == ConfirmationStatus.FINALIZED

    # "Pass 2": restart from checkpoint + 1 = 108.
    engine2 = FinalityEngine(
        chain_id=CHAIN_ID,
        confirmation_depth=DEPTH,
        provider=provider,
        engine=pg_engine,
        clock=lambda: PINNED_TIME,
        reorg_handler=reorg_handler,
    )
    checkpoint_block = await engine2.load_checkpoint()
    assert checkpoint_block == 107

    # Process blocks 108..119 (resuming from checkpoint).
    for bn in range(108, 120):
        await engine2.on_new_block(bn)

    # Verify: no duplicate facts (idempotency preserved).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        count = await repositories.count_facts_for_chain(session, CHAIN_ID)
    assert count == 3  # only the 3 facts we inserted

    # Verify: all facts eventually reach FINALIZED.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for bn in [100, 105, 110]:
            row = await repositories.get_fact(session, f"{CHAIN_ID}:0x{bn:064x}:{bn}")
            assert row is not None
            assert row.confirmation_status == ConfirmationStatus.FINALIZED

    # Verify: checkpoint advanced past 107.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        cp = await repositories.get_checkpoint(session, CHAIN_ID)
    assert cp is not None
    assert cp.last_finalized_block > 107
