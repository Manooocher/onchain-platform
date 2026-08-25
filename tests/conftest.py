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


@pytest.fixture
def clean_entities(pg_engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    """Truncate all Part A entity tables + metadata for test isolation."""

    async def _clean() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE metadata, smart_contracts, wallets, "
                    "liquidity_pools, trading_pairs, tokens CASCADE"
                )
            )

    return _clean


@pytest.fixture
def clean_outcomes(pg_engine: AsyncEngine) -> Callable[[], Awaitable[None]]:
    """Truncate outcomes + insights for test isolation (Milestone 8)."""

    async def _clean() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(text("TRUNCATE outcomes, insights CASCADE"))

    return _clean


@pytest_asyncio.fixture
async def seeded_pair(pg_engine: AsyncEngine) -> str:
    """Deterministically seed a real TradingPair belonging to this test run.

    Used by the E2E research-question test so it never depends on ambient DB
    state (a prior test may wipe trading_pairs). Returns the pair's canonical
    ID. The seeding lives here (conftest), NOT in the E2E module, so the
    E2E's own AST meta-test (which forbids persistence imports in that file)
    still passes.
    """
    from eth_utils.address import to_checksum_address
    from sqlalchemy.ext.asyncio import AsyncSession

    from onchain_platform.domain.entities.token import Token
    from onchain_platform.domain.entities.trading_pair import TradingPair
    from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
    from onchain_platform.persistence.postgres import entity_repositories as repos

    chain_id = 8453
    # A stable address derived from a fixed seed byte — deterministic across
    # runs but unlikely to collide with other tests using 0x{bb}*20 style.
    pool = to_checksum_address("0x" + "42" * 20)
    tok0 = to_checksum_address("0x4200000000000000000000000000000000000006")
    tok1 = to_checksum_address("0x" + "2b" * 20)
    pair_id = pair_canonical_id(chain_id, pool)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repos.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(chain_id, tok0),
                chain_id=chain_id,
                contract_address=tok0,
            ),
        )
        await repos.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(chain_id, tok1),
                chain_id=chain_id,
                contract_address=tok1,
            ),
        )
        await repos.save_trading_pair(
            session,
            TradingPair(
                canonical_id=pair_id,
                chain_id=chain_id,
                dex="uniswap_v2",
                base_token_id=token_canonical_id(chain_id, tok0),
                quote_token_id=token_canonical_id(chain_id, tok1),
                pool_address=pool,
                creation_block=100,
                creation_fact_id=f"{chain_id}:0x{'a1' * 32}:0",
            ),
        )
    return pair_id
