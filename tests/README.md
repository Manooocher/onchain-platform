# Testing

Comprehensive test suite ensuring correctness, determinism, and production readiness (DOC-010 § Testing, DOC-013 § Testing Conventions).

## Test Categories

### Unit Tests (`tests/unit/`)
Test individual functions and classes in isolation.

```bash
uv run pytest tests/unit/ -v
```

**Coverage:** schema validation, business logic, pure normalization functions, import contracts.

### Integration Tests (`tests/integration/`)
Test end-to-end flows against **real** databases (never mocks).

```bash
uv run pytest tests/integration/ -v
```

**Requirements:** TimescaleDB (Postgres) on `:5433` and Redis on `:6379` running, with migrations applied.

**Coverage:** database repositories, API endpoints, full pipeline flows, error handling, cursor pagination, PIT queries.

### Replay Tests (`tests/replay/`)
Verify deterministic processing — identical inputs produce byte-identical (or within-tolerance for floats) outputs.

```bash
make test-replay
```

**Purpose:**
- Detect non-determinism
- Catch hidden state dependencies
- Ensure reproducibility (ADR-006)

**How it works:**
1. Seed the database with fixed historical fixture data
2. Run the pipeline
3. Run it again with the same seed
4. Assert outputs are identical

### Schema Tests (`tests/schema/`)
Property-based tests (Hypothesis) for canonical schemas.

```bash
uv run pytest tests/schema/ -v
```

## Running Tests

```bash
# All fast tests (unit + integration + schema)
make test

# Specific category
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/schema/
make test-replay

# Live smoke test (network + real RPC)
uv run pytest -m live tests/integration/test_live_smoke.py

# With coverage
uv run pytest --cov=src/onchain_platform --cov-report=html
```

**Verified counts (current HEAD): 258 fast tests passing (+1 order-dependent skip), 7 replay, 1 live.**

## Test Naming Convention

```
test_<component>_<scenario>_<expected_outcome>
```

Examples:
- `test_finality_engine_reorg_below_confirmation_depth_marks_orphaned`
- `test_feature_engine_liquidity_growth_returns_correct_value`
- `test_outcome_engine_rug_pull_detected_on_liquidity_collapse`

## Writing Tests

Follow [DOC-013 § Testing Conventions](../../docs/013-CodingStandards.md).

### Fixtures

The root `tests/conftest.py` provides `pg_engine` (async engine to the real test DB) and `clean_facts` / `clean_entities` / `clean_outcomes` isolation fixtures. Shared canonical factories live in `tests/factories/`.

### Mocking Guidelines

- **DO** mock external services (RPC providers, third-party APIs)
- **DO** pin the clock for deterministic tests
- **DON'T** mock database operations — integration tests use the real DB
- **DON'T** mock internal functions — test real behavior

### Integration Test Template

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from onchain_platform.research.api.main import create_app
from onchain_platform.research.api.deps import get_session

async def test_example(pg_engine: AsyncEngine) -> None:
    app = create_app()

    async def _session():
        async with AsyncSession(pg_engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/health")
        assert resp.status_code == 200
```