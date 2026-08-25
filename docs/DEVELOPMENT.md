# Developer Guide

Guide for setting up a local development environment, running the quality gates, working with the database, and following the project's development conventions.

## Table of Contents
1. [Getting Started](#getting-started)
2. [Development Workflow](#development-workflow)
3. [Code Quality Gates](#code-quality-gates)
4. [Database Management](#database-management)
5. [Testing Strategy](#testing-strategy)
6. [Debugging Tips](#debugging-tips)
7. [Common Patterns](#common-patterns)

## Getting Started

### Prerequisites
- Python 3.12+
- `uv` — install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker & Docker Compose
- Git

### Initial Setup

```bash
# Clone the repository
git clone git@github.com:Manooocher/onchain-platform.git
cd onchain-platform

# Install dependencies
uv sync

# Copy the environment template
cp .env.example .env

# Start infrastructure (TimescaleDB on :5433, Redis on :6379)
docker compose up -d

# Wait for readiness
docker compose exec timescaledb pg_isready -U onchain -d onchain_platform
docker compose exec redis redis-cli ping

# Apply migrations
POSTGRES_DSN=$(grep POSTGRES_DSN .env | cut -d= -f2-) make migrate

# Verify the setup
make lint
make typecheck
make import-check
make test
```

> **Note:** `make migrate` reads the DSN from the `POSTGRES_DSN` env var (see `migrations/env.py`). Export it or set it in `.env` before running.

### Project Structure

```
onchain-platform/
├── src/onchain_platform/    # Main application code
├── tests/                   # Test suites (unit / integration / replay / schema)
├── migrations/              # Alembic database migrations
├── docs/                    # Documentation (DOC-001..015, implementation plans, ADRs)
├── config/                  # Per-chain configuration (confirmation_depth.yaml)
├── scripts/                 # Utility scripts
├── docker-compose.yml       # Infrastructure services (timescaledb, redis)
├── pyproject.toml          # Project configuration
└── Makefile                # Common commands
```

## Development Workflow

### 1. Create a Feature Branch

```bash
git checkout -b feature/my-new-feature
```

### 2. Make Changes

- Follow [DOC-013 Coding Standards](013-CodingStandards.md)
- Add tests for new functionality
- Update documentation as needed

### 3. Run Quality Gates

```bash
make lint          # Contract: ruff clean
make typecheck     # Contract: mypy strict, 0 issues
make import-check  # Contract: import-linter 8/8 KEPT
make test          # Unit + integration + schema
make test-replay   # Determinism
```

**All gates must pass before committing.**

### 4. Commit

Use [Conventional Commits](https://www.conventionalcommits.org/):

```bash
git add .
git commit -m "feat(analytics): add volatility feature

- Compute 1h rolling volatility from price returns
- Use Polars for efficient windowed calculations
- Add integration tests with deterministic fixtures

Closes #123"
```

### 5. Push and Open a PR

```bash
git push -u origin feature/my-new-feature
# Open a pull request on GitHub
```

## Code Quality Gates

### Linting (Ruff)

```bash
make lint
# Equivalent: uv run ruff check . && uv run ruff format --check .
```

Auto-fix:
```bash
uv run ruff check --fix .
uv run ruff format .
```

### Type Checking (mypy)

```bash
make typecheck
# Equivalent: uv run mypy src/
```

Runs in strict mode; the project forbids `Any` in public capability interfaces (DOC-013 § No `Any` in Capability Interfaces).

### Import Contracts (import-linter)

```bash
make import-check
# Equivalent: uv run lint-imports
```

Contracts are defined in `pyproject.toml` under `[tool.importlinter]`. **They must stay 8/8 KEPT.** Common violations to avoid:
- A capability importing `persistence`/`domain` is a bug (domain imports nothing; cross-cutting packages never import capabilities)
- `research/` importing `strategy/` (strategy owns its own router; wire via `create_app(extra_router=...)`)
- Any circular imports between capabilities

## Provider Configuration

RPC access uses a multi-provider failover pool (ADR-006, `config/providers.yaml`).
Set the provider API keys in `.env` (`ALCHEMY_BASE_API_KEY`,
`QUICKNODE_BASE_SUBDOMAIN`, `QUICKNODE_BASE_API_KEY`, `ROCKX_BASE_API_KEY`) and
select the chain at startup:

```bash
uv run python -m onchain_platform.main --chain base
uv run python -m onchain_platform.main --chain ethereum
```

Without keys, the collector falls back to the public RPC endpoint. See
[PROVIDERS.md](PROVIDERS.md) for the full provider guide.

## Database Management

### Migrations

```bash
# Apply all pending migrations
POSTGRES_DSN=postgresql+asyncpg://onchain@localhost:5433/onchain_platform make migrate

# Create a new migration
uv run alembic revision --autogenerate -m "description"

# View history
uv run alembic history
```

> Migrations are **forward-only** (DOC-014 § Migration Policy): populated historical data is never dropped by running `downgrade()`.

### Database Access

```bash
# TimescaleDB shell (the only Postgres service)
docker compose exec timescaledb psql -U onchain -d onchain_platform

# Redis CLI
docker compose exec redis redis-cli
```

### Useful Queries

```sql
-- Facts by confirmation status
SELECT confirmation_status, COUNT(*) FROM blockchain_facts GROUP BY confirmation_status;

-- Recent market bars for a pair
SELECT * FROM market_bars
WHERE pair_id = 'eip155:8453/pair:0xabc...'
ORDER BY bar_start_time DESC LIMIT 10;

-- Latest features for a pair
SELECT * FROM features
WHERE entity_id = 'eip155:8453/pair:0xabc...'
ORDER BY as_of_timestamp DESC;
```

## Testing Strategy

### When to Write Tests
- **Unit tests** — every pure function / public schema
- **Integration tests** — every repository function and API endpoint
- **Replay tests** — every capability that processes historical data (determinism)
- **Schema tests** — every canonical schema (property-based)

### Fixtures
Shared fixtures (`pg_engine`, `clean_facts`, `clean_entities`, `clean_outcomes`) live in `tests/conftest.py`. API integration tests override `get_session` with the test engine (see `tests/README.md`).

### Mocking Guidelines
- **DO** mock external services (RPC providers, third-party APIs)
- **DO** pin the clock for deterministic tests
- **DON'T** mock database operations — use the real test DB
- **DON'T** mock internal functions — test real behavior

## Debugging Tips

### Logging
The platform uses `structlog`; bind structured context (DOC-013 § Observability in Code):

```python
import structlog

logger = structlog.get_logger(__name__)

logger.info("processing_fact", fact_id=fact.fact_id, fact_type=fact.fact_type.value)
```

```bash
# Container logs
docker compose logs -f timescaledb
docker compose logs -f redis
```

### Determinism in local runs
Never read the wall clock inside a capability — pass an injected `clock`/`as_of` (DOC-013 § Determinism Discipline). `main.py` is the only sanctioned place for `datetime.now(UTC)`.

## Common Patterns

### Adding a New Feature
1. Define the schema in `domain/schemas/` (DOC-012 first if it's a new field)
2. Create a migration if it needs persistence
3. Implement computation in the owning capability (`analytics/`, `intelligence/`, etc.)
4. Add repository functions in `persistence/`
5. Expose via an API route if needed (remember the import-linter boundary)
6. Write tests (unit + integration + replay where relevant)
7. Update documentation

### Adding a New API Endpoint
Add a router file under `research/api/routes/` and mount it in `create_app()` (`research/api/main.py`). The endpoint must:
- Return a Canonical Schema (no bespoke DTOs), or a pagination envelope for collections
- Use cursor pagination, not offset
- Declare `summary`/`description` and typed `Enum` query params (DOC-015 § OpenAPI)

If the endpoint belongs to another capability (e.g. Strategy), that capability owns the router and it is injected via `create_app(extra_router=...)` in the composition root.

### Error Handling
Crossing a capability boundary? Raise a `PlatformError` subclass (`domain/exceptions.py`). The API maps these to `{error: {code, message, correlation_id}}` automatically.

## Resources
- [Architecture](ARCHITECTURE.md)
- [API Reference](API.md)
- [Domain Model](../docs/006-DomainModel.md)
- [Canonical Schemas](../docs/012-CanonicalSchema.md)
- [Coding Standards](../docs/013-CodingStandards.md)
- [API Contracts](../docs/015-APIContracts.md)