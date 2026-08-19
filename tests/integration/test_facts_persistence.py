"""Integration tests: persistence translation boundary against REAL Postgres.

Integration tests run against real infrastructure, never mocks (DOC-010 §
Integration Tests, DOC-011 § tests). Naming: test_<unit>_<scenario>_<
expected_outcome> (DOC-013 § Testing Conventions).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.persistence.postgres import repositories
from tests.factories.blockchain_fact import blockchain_fact


@pytest.fixture
def clean_facts(pg_engine: AsyncEngine):
    """Truncate blockchain_facts before each test (test isolation only —
    nothing here ever touches FINALIZED rows of real data; the table is
    disposable test infrastructure)."""

    async def _clean() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(text("TRUNCATE blockchain_facts"))

    return _clean


async def test_save_fact_inserts_row_readable_byte_identical(
    pg_engine: AsyncEngine, clean_facts
) -> None:
    await clean_facts()
    fact = blockchain_fact()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        inserted = await repositories.save_fact(session, fact)
    assert inserted is True

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        restored = await repositories.get_fact(session, fact.fact_id)

    assert restored is not None
    # Every field byte-identical — zero tolerance (DOC-010 § Testing:
    # Decimal/String fields; all M1 fields are str/int/enum).
    assert restored == fact
    assert restored.payload == fact.payload
    assert restored.event_time == fact.event_time
    assert restored.tx_hash == fact.tx_hash
    assert restored.block_hash == fact.block_hash


async def test_save_fact_twice_is_idempotent_no_duplicate(
    pg_engine: AsyncEngine, clean_facts
) -> None:
    # ADR-006 § Idempotency: processing the same event multiple times must
    # produce the same final system state — proven, not assumed.
    await clean_facts()
    fact = blockchain_fact()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        first = await repositories.save_fact(session, fact)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        second = await repositories.save_fact(session, fact)

    assert first is True
    assert second is False  # ON CONFLICT DO NOTHING — no duplicate row

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        count = await repositories.count_facts_for_chain(session, fact.chain_id)
    assert count == 1


async def test_list_facts_for_chain_returns_deterministic_order(
    pg_engine: AsyncEngine, clean_facts
) -> None:
    # DOC-013 § Determinism Discipline: ordered iteration only.
    await clean_facts()
    # Insert out of order deliberately.
    facts = [
        blockchain_fact(log_index=9, block_number=100, tx_hash=f"0x{'aa' * 32}"),
        blockchain_fact(log_index=3, block_number=100, tx_hash=f"0x{'bb' * 32}"),
        blockchain_fact(log_index=1, block_number=50, tx_hash=f"0x{'cc' * 32}"),
    ]
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for f in facts:
            await repositories.save_fact(session, f)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        rows = await repositories.list_facts_for_chain(session, 8453)

    assert [f.block_number for f in rows] == [50, 100, 100]
    assert [f.log_index for f in rows] == [1, 3, 9]


async def test_get_fact_missing_returns_none(pg_engine: AsyncEngine, clean_facts) -> None:
    await clean_facts()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await repositories.get_fact(session, "8453:0x" + "00" * 32 + ":0")
    assert result is None
