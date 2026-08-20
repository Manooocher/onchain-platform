"""Integration tests: confirmation lifecycle + checkpoint persistence.

Tests run against REAL Postgres (DOC-010 § Integration Tests, DOC-011 §
tests). Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 §
Testing Conventions).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.schemas.checkpoint import Checkpoint
from onchain_platform.domain.schemas.enums import ConfirmationStatus
from onchain_platform.persistence.postgres import repositories
from tests.factories.blockchain_fact import blockchain_fact


async def test_advance_confirmation_counts_pending_to_confirmed_to_finalized(
    pg_engine: AsyncEngine, clean_facts: Callable[[], Awaitable[None]]
) -> None:
    # ADR-006 § Confirmation Lifecycle: PENDING → CONFIRMED → FINALIZED.
    await clean_facts()
    fact = blockchain_fact(block_number=100)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.save_fact(session, fact)

    # Head = 101, depth = 3 → confirmations = 1 → CONFIRMED.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.advance_confirmation_counts(session, 8453, 101, 3)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        row = await repositories.get_fact(session, fact.fact_id)
    assert row is not None
    assert row.confirmation_status == ConfirmationStatus.CONFIRMED
    assert row.confirmations == 1

    # Head = 103, depth = 3 → confirmations = 3 → FINALIZED.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.advance_confirmation_counts(session, 8453, 103, 3)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        row = await repositories.get_fact(session, fact.fact_id)
    assert row is not None
    assert row.confirmation_status == ConfirmationStatus.FINALIZED
    assert row.confirmations == 3


async def test_advance_confirmation_counts_skips_finalized_rows(
    pg_engine: AsyncEngine, clean_facts: Callable[[], Awaitable[None]]
) -> None:
    # DOC-013 § Immutability: FINALIZED rows are never touched.
    await clean_facts()
    fact = blockchain_fact(block_number=100)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.save_fact(session, fact)
        await repositories.advance_confirmation_counts(session, 8453, 103, 3)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        row = await repositories.get_fact(session, fact.fact_id)
    assert row is not None
    assert row.confirmation_status == ConfirmationStatus.FINALIZED
    assert row.confirmations == 3

    # Advance again — FINALIZED row must not change.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.advance_confirmation_counts(session, 8453, 200, 3)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        row2 = await repositories.get_fact(session, fact.fact_id)
    assert row2 is not None
    assert row2.confirmations == 3  # unchanged
    assert row2.confirmation_status == ConfirmationStatus.FINALIZED


async def test_mark_facts_orphaned_leaves_finalized_untouched(
    pg_engine: AsyncEngine, clean_facts: Callable[[], Awaitable[None]]
) -> None:
    # ADR-006 § Orphaned: only PENDING/CONFIRMED facts in the range are
    # marked ORPHANED. FINALIZED facts before the fork point are untouched.
    await clean_facts()
    fact_finalized = blockchain_fact(block_number=100, tx_hash=f"0x{'aa' * 32}", log_index=1)
    fact_pending = blockchain_fact(block_number=105, tx_hash=f"0x{'bb' * 32}", log_index=2)

    # Insert both, then finalize only fact_finalized (block 100, head=103,
    # depth=3 → confirmations=3 → FINALIZED). fact_pending (block 105) has
    # confirmations=103-105 which is negative — so use head=106 instead:
    # fact_finalized gets 6 confirmations (FINALIZED), fact_pending gets 1
    # (CONFIRMED, not yet FINALIZED).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.save_fact(session, fact_finalized)
        await repositories.save_fact(session, fact_pending)
        await repositories.advance_confirmation_counts(session, 8453, 106, 3)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        f1 = await repositories.get_fact(session, fact_finalized.fact_id)
        f2 = await repositories.get_fact(session, fact_pending.fact_id)
    assert f1 is not None and f1.confirmation_status == ConfirmationStatus.FINALIZED
    assert f2 is not None and f2.confirmation_status == ConfirmationStatus.CONFIRMED

    # Orphan range [104, 110] — should mark fact_pending ORPHANED but leave
    # fact_finalized untouched (it's FINALIZED, excluded by the WHERE clause).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        count = await repositories.mark_facts_orphaned(session, 8453, 104, 110)
    assert count == 1  # only fact_pending (block 105)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        f1 = await repositories.get_fact(session, fact_finalized.fact_id)
        f2 = await repositories.get_fact(session, fact_pending.fact_id)
    assert f1 is not None and f1.confirmation_status == ConfirmationStatus.FINALIZED
    assert f2 is not None and f2.confirmation_status == ConfirmationStatus.ORPHANED
    assert f2.confirmations == 0


async def test_checkpoint_round_trip_and_upsert(
    pg_engine: AsyncEngine,
) -> None:
    # DOC-012 § B.0: mutable singleton per chain.
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)
    cp = Checkpoint(
        chain_id=8453,
        last_finalized_block=13_500_000,
        last_finalized_at=now,
        updated_at=now,
    )

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.save_checkpoint(session, cp)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        loaded = await repositories.get_checkpoint(session, 8453)
    assert loaded is not None
    assert loaded.chain_id == 8453
    assert loaded.last_finalized_block == 13_500_000

    # Upsert: overwrite in place.
    later = datetime(2026, 8, 19, 13, 0, 0, tzinfo=UTC)
    cp2 = Checkpoint(
        chain_id=8453,
        last_finalized_block=13_500_010,
        last_finalized_at=later,
        updated_at=later,
    )
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repositories.save_checkpoint(session, cp2)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        loaded2 = await repositories.get_checkpoint(session, 8453)
    assert loaded2 is not None
    assert loaded2.last_finalized_block == 13_500_010


async def test_get_checkpoint_missing_returns_none(
    pg_engine: AsyncEngine,
) -> None:
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await repositories.get_checkpoint(session, 999)
    assert result is None
