"""Integration tests: Outcome persistence against REAL Postgres.

Integration tests run against real infrastructure, never mocks (DOC-010 §
Integration Tests, DOC-011 § tests). Naming: test_<unit>_<scenario>_<
expected_outcome> (DOC-013 § Testing Conventions).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.schemas.enums import OutcomeType
from onchain_platform.domain.schemas.outcome import Outcome
from onchain_platform.persistence.postgres.outcomes_insights import (
    get_latest_outcome,
    list_outcomes_for_entity,
    save_outcome,
)

ENTITY_ID = "eip155:8453/pair:0x39f0E675D479088DE08b7f201Ac08e20F899B838"
PINNED = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


def _make_outcome(
    *,
    outcome_type: OutcomeType = OutcomeType.RUG_PULL,
    evaluation_timestamp: datetime = PINNED,
    label_value: bool = True,
) -> Outcome:
    return Outcome.create(
        entity_id=ENTITY_ID,
        outcome_type=outcome_type,
        observation_window="1h",
        label_definition="Liquidity drops >90% OR honeypot detected",
        label_definition_version="1.0",
        evaluation_timestamp=evaluation_timestamp,
        evaluated_at=PINNED,
        label_value=label_value,
    )


async def test_save_outcome_inserts_row_readable_byte_identical(
    pg_engine: AsyncEngine, clean_outcomes: Callable[[], Awaitable[None]]
) -> None:
    await clean_outcomes()
    outcome = _make_outcome()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        inserted = await save_outcome(session, outcome)
    assert inserted is True

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        rows = await list_outcomes_for_entity(session, ENTITY_ID)

    assert len(rows) == 1
    # Every field byte-identical — zero tolerance (DOC-010 § Testing).
    assert rows[0] == outcome
    assert rows[0].label_definition_version == "1.0"
    assert rows[0].label_value is True


async def test_save_outcome_idempotent_no_duplicate(
    pg_engine: AsyncEngine, clean_outcomes: Callable[[], Awaitable[None]]
) -> None:
    # ADR-006 § Idempotency: re-evaluation must never duplicate an outcome.
    await clean_outcomes()
    outcome = _make_outcome()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        first = await save_outcome(session, outcome)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        second = await save_outcome(session, outcome)

    assert first is True
    assert second is False  # ON CONFLICT DO NOTHING

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        rows = await list_outcomes_for_entity(session, ENTITY_ID)
    assert len(rows) == 1


async def test_save_outcome_same_entity_different_types_coexist(
    pg_engine: AsyncEngine, clean_outcomes: Callable[[], Awaitable[None]]
) -> None:
    # A pair can carry RUG_PULL + SUCCESSFUL_LAUNCH + DEAD_TOKEN labels.
    await clean_outcomes()
    for t in (OutcomeType.RUG_PULL, OutcomeType.SUCCESSFUL_LAUNCH, OutcomeType.DEAD_TOKEN):
        o = _make_outcome(outcome_type=t)
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            await save_outcome(session, o)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        all_rows = await list_outcomes_for_entity(session, ENTITY_ID)
        rug_only = await list_outcomes_for_entity(session, ENTITY_ID, OutcomeType.RUG_PULL)
        deadline = await get_latest_outcome(session, ENTITY_ID, OutcomeType.DEAD_TOKEN)

    assert len(all_rows) == 3
    assert len(rug_only) == 1
    assert deadline is not None
    assert deadline.outcome_type == OutcomeType.DEAD_TOKEN


async def test_get_latest_outcome_returns_most_recent(
    pg_engine: AsyncEngine, clean_outcomes: Callable[[], Awaitable[None]]
) -> None:
    await clean_outcomes()
    t1 = datetime(2026, 8, 22, 11, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await save_outcome(session, _make_outcome(evaluation_timestamp=t1, label_value=False))
        await save_outcome(session, _make_outcome(evaluation_timestamp=t2, label_value=True))

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        latest = await get_latest_outcome(session, ENTITY_ID, OutcomeType.RUG_PULL)

    assert latest is not None
    assert latest.evaluation_timestamp == t2
    assert latest.label_value is True


async def test_get_latest_outcome_missing_returns_none(
    pg_engine: AsyncEngine, clean_outcomes: Callable[[], Awaitable[None]]
) -> None:
    await clean_outcomes()
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await get_latest_outcome(session, ENTITY_ID, OutcomeType.DEAD_TOKEN)
    assert result is None
