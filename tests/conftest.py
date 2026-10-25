"""Root pytest configuration shared by all four test categories.

Test layout mirrors DOC-010 § Testing / DOC-011 § tests: unit/, integration/
(real Postgres/Redis, never mocks), replay/ (fixed fixtures, byte-identical
assertions), schema/ (hypothesis property tests).

Integration and replay tests that need PostgreSQL read the DSN from the
POSTGRES_DSN environment variable (default matches docker-compose.yml) —
DOC-013 § Dependency & Composition: configuration is passed in, never
hardcoded.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_POSTGRES_DSN = "postgresql+asyncpg://onchain@localhost:5433/onchain_platform"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# Hosts for which we may auto-derive a *_test DSN (S0-5 refinement 4). For any
# other host an explicit TEST_POSTGRES_DSN is required — we never guess a test
# database name on a remote/production host.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", None, ""}
# Dedicated Redis logical db for tests (S0-5 refinement 1).
_TEST_REDIS_DB = "15"


def _db_name(dsn: str) -> str:
    return urlsplit(dsn).path.lstrip("/")


def _require_test_postgres(dsn: str) -> None:
    """Fail fast unless the DSN targets a database whose name ends with _test."""
    name = _db_name(dsn)
    if not name.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run tests against non-test database {name!r}: the test "
            "database name must end with '_test' (S0-5). Set TEST_POSTGRES_DSN."
        )


def _derive_test_postgres_dsn() -> str:
    explicit = os.environ.get("TEST_POSTGRES_DSN")
    if explicit:
        dsn = explicit
    else:
        base = os.environ.get("POSTGRES_DSN", DEFAULT_POSTGRES_DSN)
        parts = urlsplit(base)
        if parts.hostname not in _LOCAL_HOSTS:
            raise RuntimeError(
                f"Refusing to auto-derive a test DSN for non-local host "
                f"{parts.hostname!r} (S0-5): set TEST_POSTGRES_DSN explicitly."
            )
        name = _db_name(base)
        test_name = name if name.endswith("_test") else f"{name}_test"
        dsn = urlunsplit(parts._replace(path=f"/{test_name}"))
    _require_test_postgres(dsn)
    return dsn


def _derive_test_redis_url() -> str:
    base = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
    explicit = os.environ.get("TEST_REDIS_URL")
    url = explicit if explicit else urlunsplit(urlsplit(base)._replace(path=f"/{_TEST_REDIS_DB}"))
    # Fail fast if the test Redis would share the dev logical db (S0-5).
    prod_db = urlsplit(base).path.lstrip("/") or "0"
    test_db = urlsplit(url).path.lstrip("/") or "0"
    if test_db == prod_db:
        raise RuntimeError(
            f"Refusing to run tests against Redis db {test_db!r}: it is the same "
            "as the configured REDIS_URL db. Use a dedicated test db (e.g. /15)."
        )
    return url


# The development DSN BEFORE rewrite — used only as a maintenance connection to
# CREATE the test database (issuing CREATE DATABASE touches no dev-schema rows).
_MAINTENANCE_DSN = os.environ.get("POSTGRES_DSN", DEFAULT_POSTGRES_DSN)
_TEST_POSTGRES_DSN = _derive_test_postgres_dsn()
_TEST_REDIS_URL = _derive_test_redis_url()

# Rewrite the environment at import time — BEFORE any test module is collected —
# so code that reads these vars directly (scripts, module-level constants, the
# alembic env) all land on the test instances (S0-5 refinement 3).
os.environ["POSTGRES_DSN"] = _TEST_POSTGRES_DSN
os.environ["REDIS_URL"] = _TEST_REDIS_URL


def postgres_dsn() -> str:
    return _TEST_POSTGRES_DSN


def redis_url() -> str:
    return _TEST_REDIS_URL


def _asyncpg_dsn(sqlalchemy_dsn: str) -> str:
    """asyncpg.connect wants a plain postgresql:// URL (no +asyncpg driver)."""
    return urlunsplit(urlsplit(sqlalchemy_dsn)._replace(scheme="postgresql"))


async def _create_test_database_and_extension() -> None:
    import asyncpg

    test_name = _db_name(_TEST_POSTGRES_DSN)
    conn = await asyncpg.connect(_asyncpg_dsn(_MAINTENANCE_DSN))
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", test_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{test_name}"')
    finally:
        await conn.close()

    conn = await asyncpg.connect(_asyncpg_dsn(_TEST_POSTGRES_DSN))
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def _provision_test_database() -> None:
    """Ensure the *_test database exists with the TimescaleDB extension and the
    full schema (alembic upgrade head). NOT autouse — only Postgres-backed
    fixtures depend on it, so a no-service `-m unit` run never touches Postgres.

    Runs in a sync context (no running event loop): asyncio.run() for the
    asyncpg provisioning, and alembic in a subprocess (its env runs its own
    asyncio loop, and it inherits the already-rewritten POSTGRES_DSN).
    """
    import shutil
    import subprocess
    import sys

    asyncio.run(_create_test_database_and_extension())
    alembic_bin = shutil.which("alembic") or str(Path(sys.executable).parent / "alembic")
    subprocess.run([alembic_bin, "upgrade", "head"], cwd=str(_REPO_ROOT), check=True)


@pytest_asyncio.fixture
async def pg_engine(_provision_test_database: None) -> AsyncIterator[AsyncEngine]:
    """Async engine against the real local Postgres/TimescaleDB container.

    Integration tests run against real infrastructure, not mocks
    (DOC-010 § Integration Tests, DOC-011 § tests). Depending on
    `_provision_test_database` guarantees the *_test schema exists first.
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


# --- Test categorisation (S0-6) ----------------------------------------------
# Auto-apply a category marker to every test based on its directory, so CI can
# select by marker (unit with no services; integration/replay with services;
# live only on demand). Existing markers (e.g. `live`) are preserved.
_DIR_MARKERS = {
    "unit": "unit",
    "schema": "unit",  # hypothesis property tests — no I/O
    "integration": "integration",
    "replay": "replay",
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    tests_root = Path(__file__).resolve().parent
    for item in items:
        try:
            rel = Path(str(item.fspath)).resolve().relative_to(tests_root)
        except ValueError:
            continue
        top = rel.parts[0] if rel.parts else ""
        marker = _DIR_MARKERS.get(top)
        if marker:
            item.add_marker(getattr(pytest.mark, marker))
