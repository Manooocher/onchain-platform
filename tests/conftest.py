"""Root pytest configuration shared by all four test categories.

Test layout mirrors DOC-010 § Testing / DOC-011 § tests: unit/, integration/
(real Postgres/Redis, never mocks), replay/ (fixed fixtures, byte-identical
assertions), schema/ (hypothesis property tests).

Integration and replay tests that need PostgreSQL read the DSN from the
POSTGRES_DSN environment variable (default matches docker-compose.yml) —
DOC-013 § Dependency & Composition: configuration is passed in, never
hardcoded.
"""

import os
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DEFAULT_POSTGRES_DSN = "postgresql+asyncpg://onchain@localhost:5433/onchain_platform"


def postgres_dsn() -> str:
    return os.environ.get("POSTGRES_DSN", DEFAULT_POSTGRES_DSN)


@pytest_asyncio.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    """Async engine against the real local Postgres/TimescaleDB container.

    Integration tests run against real infrastructure, not mocks
    (DOC-010 § Integration Tests, DOC-011 § tests).
    """
    engine = create_async_engine(postgres_dsn())
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def clean_facts(pg_engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    """Truncate blockchain_facts before each test (test isolation only —
    nothing here ever touches FINALIZED rows of real data; the table is
    disposable test infrastructure shared by integration and replay tests)."""

    async def _clean() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(text("TRUNCATE blockchain_facts"))

    return _clean
