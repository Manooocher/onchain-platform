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
from collections.abc import AsyncIterator

import pytest_asyncio
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
