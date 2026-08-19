# Milestone 1 Execution Plan — The Walking Skeleton

Status: Planning artifact. Supersedes nothing; implements `docs/implementation/ImplementationPlan.md` § Milestone 1.
Prepared: 2026-08-19, after re-reading ImplementationPlan § Milestone 1, DOC-006/008/010/011/012/013/014, ADR-006.

Goal (verbatim from ImplementationPlan): one real `PairCreated` event on Base → one real row in
`blockchain_facts` → idempotent replay produces the identical row → first Replay Test passes → `lint-imports` passes.

---

## 0. Pre-Flight Status

Verified against the actual repo state (commands run, not assumed): `uv run lint-imports` → 8 contracts KEPT,
`uv run ruff check .` → clean, `uv run ruff format --check .` → clean, `uv run mypy src/` → clean (19 files).

| Day 0 item (ImplementationPlan § Day 0) | Status | Detail / completion step |
|---|---|---|
| `uv init` + DOC-010 dependency set | ⚠️ Partial | `pyproject.toml` has all Day-0-named runtime deps (pydantic, sqlalchemy, alembic, web3, redis, polars, fastapi, streamlit, structlog, httpx) + dev set (pytest, pytest-asyncio, hypothesis, ruff, mypy, import-linter). Gaps: `sqlalchemy[asyncio]` has no async driver installed (need `asyncpg`), and DOC-010 § Security mandates Pydantic Settings (separate `pydantic-settings` package under Pydantic v2) — not present. Fix before M1 code: `uv add asyncpg pydantic-settings`. |
| Scaffold tree matches DOC-011 | ⚠️ Partial | All 19 package `__init__.py` present (acquisition/providers, analytics, domain/entities, domain/schemas, domain_management, intelligence, persistence/postgres, persistence/timescale, platform, processing, research/api, research/dashboard, strategy, transport). Missing vs DOC-011: `src/onchain_platform/main.py` (created in M1 build step 17), `tests/` tree (step 12), `.github/workflows/` (step 7). Module files (`domain/enums.py`, `domain/exceptions.py`, …) are created when their build step arrives — correct per Day 0 ("empty packages, no logic yet"). |
| `docker-compose.yml` with Postgres+TimescaleDB+Redis | ❌ Missing | Create at repo root (DOC-011 § Supporting Directories sanctions root for zero-flag `docker compose up`; AGENTS.md `make run` assumes it). Services: `timescale/timescaledb` image (Postgres 16 + extension), `redis`. Verification: `docker compose up -d` then `SELECT extversion FROM pg_extension WHERE extname='timescaledb'` returns a row (Day 0 requires the extension ENABLED, not just installed). |
| Alembic initialized + empty baseline migration | ❌ Missing | `uv run alembic init migrations` (DOC-011 top-level `migrations/`), configure `migrations/env.py` to read the DSN from environment (never commit a DSN with credentials), produce one empty baseline migration. Real table migrations start at M1 build step 14 (ImplementationPlan § Day 0, verbatim). |
| import-linter contracts in `pyproject.toml` AND passing | ✅ Done | Contracts present and byte-identical to DOC-011 § Enforcing the Dependency Rule (1 layers + 7 forbidden); `lint-imports` passes against the empty tree (verified above). |
| `.env.example` with placeholder RPC key | ❌ Missing | Create with: `RPC_URL` (default `https://mainnet.base.org`), `ALCHEMY_API_KEY=<your-key-here>` (ImplementationPlan § Day 0 names one RPC provider key), `POSTGRES_DSN`, `REDIS_URL`. `.env` already git-ignored (verified `.gitignore`). |
| `Makefile` targets per DOC-011 | ❌ Missing | Create with all 8 DOC-011 § Makefile targets: install, lint, typecheck, test, test-replay, import-check, run, migrate. `make test` excludes `tests/replay/` (DOC-011, deliberate). |
| Git commit #1 passes CI | ⚠️ Partial | Commit f8c6d2c exists and all three local gates pass on it. But: no `.github/workflows/` CI file exists (ImplementationPlan § Continuous Practices: "lint-imports runs in CI from Day 0"), and `.opencode/`, `AGENTS.md`, `docs/implementation/` are still untracked. Completion: add minimal CI workflow (lint + typecheck + import-check + test), commit Day-0 completion as its own commit. |

Additional tooling gaps found (DOC-013 requirements, no home yet):

| Gap | Fix |
|---|---|
| No mypy config; DOC-013 § Determinism Discipline requires `--strict` | Add `[tool.mypy] strict = true` (scoped to `src/` + `tests/`) in `pyproject.toml`. |
| No ruff config | Add `[tool.ruff]` with line-length and a standard rule set; keep `ruff format` as the formatter. |
| No pytest config / asyncio mode | Add `[tool.pytest.ini_options]` with `asyncio_mode = "auto"`, `testpaths`, and markers (`live` for network tests). |
| `pyproject [project.scripts]` points at `onchain_platform:main` (hello-world stub in `__init__.py`) | Repoint to `onchain_platform.main:main` in build step 17; delete the stub. |

All of Section 0 is completed and committed BEFORE any Capability code (Day 0 discipline).

---

## 1. Open Decisions — Resolved

| Decision | Required For | Resolution | Rationale |
|---|---|---|---|
| First RPC provider (Base free tier) | Collector | Primary: **generic JSON-RPC over HTTPX**, implemented as `acquisition/providers/local_node.py` against a plain endpoint (default: Base public endpoint `https://mainnet.base.org`, overridable via `RPC_URL`). Alchemy is the documented swap-in once an API key exists (`acquisition/providers/alchemy.py`, later). | ImplementationPlan § Open Decisions names Alchemy "a reasonable default" but a key is a human action item and must not block the build; `local_node.py` is in DOC-011's provider list and implements ADR-006 Option A (raw JSON-RPC) with zero vendor coupling — the purest expression of ADR-006 Principle 6. DOC-010 § Blockchain Connectivity selects HTTPX for RPC calls. web3.py stays installed (Day 0 set) but unused for now; DOC-010 explicitly demotes it to "implementation detail behind the interface". |
| `avg_block_time_seconds` seed for Base | `Blockchain` row | Base = 2.0, Ethereum = 12.0, BNB Chain = 0.75 — seeded for all three EVM-first chains, stored in `blockchains`, treated as correctable config values. | ImplementationPlan § Open Decisions: "Seed with each chain's current published average and treat as a config value to correct later." Base ≈ 2s is corroborated by DOC-006's own example ("~2s blocks"). Seed rows go in the M1 migration (Part A table, mutable, standard migrations allowed — DOC-014 § Migration Policy). |
| PairCreated factory contract address(es) on Base | Collector filter | **Milestone 1 uses exactly ONE factory**: `0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6` — live-verified today via `eth_getLogs` to emit canonical Uniswap-V2-style `PairCreated(address token0, address token1, address pair, uint)` events (dense: 737 events per 10k blocks near block 13.5M; >3M pairs created to date). Registry of further Base factories for later milestones: BaseSwap V2 (`0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB`), SushiSwap V2 (`0x71524B4f93c58fcbF659783284E38825f0622859`) — attributions from memory, NOT live-verified, verify on Basescan before use; Aerodrome emits `PoolCreated` (different signature incl. fee tier) and needs its own normalization, deferred. | ImplementationPlan § Milestone 1: "polls or subscribes for PairCreated logs on **one factory contract**" — one is the spec, not a shortcut. The V2 `PairCreated` ABI is identical across V2 forks, so the normalizer written now carries forward. Build-time checklist item: confirm DEX attribution of the chosen address on Basescan before fixing the payload `dex` string (§6 Q1). |
| Block range for first Replay Test fixture | Replay test | **Blocks 13,500,000–13,500,024 (25 blocks) on Base**, restricted to the chosen factory + `PairCreated` topic. Live-verified today: contains exactly 5 real `PairCreated` events across blocks {13500004, 13500010, 13500017, 13500020, 13500022}; sample event: block 13,500,004, tx `0xfc6bbb0b…f54fbd`, logIndex 43, block timestamp 1713789355 (2024-04-22), deep-finalized history. | Small enough to commit as JSON, large enough to contain multiple events in multiple blocks (exercises block iteration + ordering). Fixed real data satisfies ImplementationPlan DoD ("one small, fixed, real block range"). |

Flagged assumptions (per constraints, nothing here is invented beyond what's cited): the fixture range and factory
behavior above are from live read-only probes of `https://mainnet.base.org` performed during this planning session;
they are re-verified as build step 13 before being frozen into fixtures.

---

## 2. Build Order (Sequential)

Dependency rule for the whole sequence: each step must pass `make lint`, `make typecheck`, and `make import-check`
before the next begins (ImplementationPlan § Continuous Practices — gates from commit 1, not "once big enough").

### Phase 0-completion (repo tooling, no Capability code)

1. **`pyproject.toml`** — add `asyncpg`, `pydantic-settings`; add `[tool.mypy] strict=true`, `[tool.ruff]`,
   `[tool.pytest.ini_options]` (asyncio auto mode, `live` marker). Deps: none. Verification: `uv sync && uv run mypy
   src/ && uv run ruff check .` pass; `uv run python -c "import asyncpg, pydantic_settings"`. Complexity: trivial.
2. **`Makefile`** — 8 targets exactly per DOC-011 § Makefile. Deps: step 1. Verification: each target runs
   (`make test` passes vacuously with zero tests collected — set `--co` tolerance or a placeholder assertion).
   Complexity: trivial.
3. **`docker-compose.yml`** — timescaledb (pg16) + redis, healthchecks, ports, volume. Deps: none. Verification:
   `make run`, then psql: `SELECT extversion FROM pg_extension WHERE extname='timescaledb';` and `redis-cli ping`.
   Complexity: trivial.
4. **`migrations/` (alembic init)** — `alembic init migrations`, env.py reads DSN from env var, one empty baseline
   migration. Deps: step 1. Verification: `make migrate` runs the empty baseline against the compose DB cleanly.
   Complexity: trivial.
5. **`.env.example`** — placeholders only (DOC-010 § Security: secrets never committed). Deps: none. Verification:
   review; `.env` remains git-ignored. Complexity: trivial.
6. **`config/`** — NOT created yet. `confirmation_depth.yaml` is Milestone 2's (ImplementationPlan § Milestone 2).
   Recording so nobody creates it early. Complexity: n/a.
7. **`.github/workflows/ci.yml`** — runs lint, typecheck, import-check, and `make test` on the project's Python 3.12
   (`.python-version` verified = 3.12). Integration/replay jobs need services; wire those as follow-up once tests
   exist (steps 16/18) — flagged in §6 Q4. Deps: step 2. Verification: workflow file lints (`actionlint` if
   available, else review). Complexity: trivial.
8. **Commit** Day-0 completion. Deps: 1–7. Verification: `git status` clean, all gates green on the commit.

### Domain layer (bottom of the dependency graph — DOC-011)

9. **`src/onchain_platform/domain/exceptions.py`** — `PlatformError` + the five subclasses exactly as DOC-013 §
   Exception Hierarchy specifies (DomainValidationError, PersistenceError, AcquisitionError, SchemaVersionError,
   TransportError), docstrings included. Deps: step 8. Verification: import test; `mypy --strict` clean. Complexity:
   trivial.
10. **`src/onchain_platform/domain/schemas/enums.py`** — `ConfirmationStatus` and `FactType` as `StrEnum`
    (PENDING/CONFIRMED/FINALIZED/ORPHANED; PAIR_CREATED/SWAP_EXECUTED/LIQUIDITY_ADDED/LIQUIDITY_REMOVED) per DOC-012
    § B.1. NOTE on file location: ImplementationPlan § Milestone 1 and this task both specify
    `domain/schemas/enums.py`; DOC-011's tree shows `domain/enums.py` (for ChainId/EntityType). Resolution:
    fact-lifecycle enums live in `domain/schemas/enums.py` (two documents agree); `domain/enums.py` is reserved for
    the ChainId/EntityType DOC-011 names (Milestone 4). Logged so spec-auditor sees it is deliberate (§6 Q2).
    Deps: step 9. Verification: unit test asserting member values match DOC-012 § B.1 literals. Complexity: trivial.
11. **`src/onchain_platform/domain/schemas/blockchain_fact.py`** — `PairCreatedPayload` + `BlockchainFact` typed
    verbatim from DOC-012 § B.1: discriminated union via `Field(discriminator="fact_type")` (the pattern is
    normative, DOC-012 § Modeling the discriminated payload); `model_config = ConfigDict(frozen=True)` (DOC-013 §
    Immutability); validators, all pure (DOC-013): (a) reject naive datetimes — pydantic v2 accepts them by default,
    so an explicit validator is required (DOC-012 § Conventions: "a naive datetime is a validation error"); (b)
    `fact_id` format `"{chain_id}:{tx_hash}:{log_index}"` consistency with the component fields; (c) EIP-55
    checksummed addresses on payload fields (DOC-012 § Conventions) using `eth_utils` pure functions — flagged
    assumption §6 Q3; (d) `payload.fact_type == fact_type` sync check. SwapExecuted/Liquidity payload classes are
    defined too (the DOC-012 union is one artifact), but nothing else in M1 consumes them. Deps: step 10.
    Verification: `tests/schema/` hypothesis tests (step 12) + targeted unit tests (valid log → valid model; naive
    timestamp → error; non-checksummed address → error; frozen mutation → error). Complexity: moderate.
12. **`tests/` scaffolding + `tests/factories/`** — create `tests/{unit,integration,replay/fixtures,schema}/` and
    `tests/factories/blockchain_fact.py` builder (every field defaulted correctly; DOC-013 § Testing Conventions:
    factories exist precisely so no test hand-types a Decimal/field). Deps: step 1. Verification: pytest collects.
    Complexity: trivial.
13. **`tests/schema/test_canonical_schemas.py`** — hypothesis property tests for `BlockchainFact`/
    `PairCreatedPayload` round-trip (`model_dump` → `model_validate` byte-identical for str fields), naive-datetime
    rejection, discriminator dispatch. DOC-010 § Testing (Schema Validation Tests), DOC-011 tests tree. Deps: step
    11. Verification: `make test` green. Complexity: moderate.

### Persistence layer

14. **`src/onchain_platform/persistence/postgres/facts.py` + migration** —
    - ORM model `BlockchainFactRow` for `blockchain_facts` per DOC-014: `fact_id TEXT PK`, `schema_version TEXT`,
      `chain_id BIGINT`, `fact_type` native PG ENUM, `block_number BIGINT`, `block_hash VARCHAR(66)`, `tx_hash
      VARCHAR(66)`, `log_index BIGINT`, three `TIMESTAMPTZ` columns, `confirmation_status` native PG ENUM,
      `confirmations BIGINT CHECK (>=0)`, `payload JSONB`, `involved_wallets TEXT[] GENERATED ALWAYS AS (…) STORED`
      (DOC-014 § wallet involvement — trigger condition already met) with GIN index; indexes `(chain_id,
      confirmation_status)` and `(chain_id, block_number)` (DOC-014 § Indexing Strategy).
    - Insert path ONLY (ImplementationPlan § Milestone 1: "insert path only"): `INSERT … ON CONFLICT (fact_id) DO
      NOTHING` (ADR-006 § Persistence Rules) wrapped so SQLAlchemy errors surface as `PersistenceError` (DOC-013 §
      Exception Hierarchy). No UPDATE path exists yet — nothing in M1 may update a fact row.
    - Alembic migration hand-written (autogenerate does not reliably emit PG ENUMs, generated columns, GIN);
      additive-only discipline starts here (DOC-014 § Migration Policy).
    Deps: steps 4, 11. Verification: `make migrate` on a fresh container; `\d blockchain_facts` shows every column
    type matching DOC-014's mapping table; manual INSERT via psql round-trips. Complexity: high (most likely place
    for friction — see Risk Register).
15. **`src/onchain_platform/persistence/postgres/models.py` + migration + seed** — `Blockchain` entity table per
    DOC-012 Part A (`chain_id` PK, `name`, `native_asset_symbol`, `is_supported`, `avg_block_time_seconds DOUBLE
    PRECISION` per DOC-014's genuinely-float category) + seed rows (Base 2.0 / Ethereum 12.0 / BNB 0.75, §1).
    Deps: step 14 (shares migration history). Verification: `SELECT * FROM blockchains` = 3 rows. Complexity: trivial.
16. **`src/onchain_platform/persistence/postgres/repositories.py`** — translation boundary `BlockchainFact` (domain)
    ↔ `BlockchainFactRow` (ORM): `save_fact(session, fact)` returning inserted-or-skipped; returns domain types only,
    never leaks an ORM instance upward (DOC-011 persistence section; DOC-010 § Persistence Access Layer
    constraint). Session is a parameter, never an imported global (DOC-013 § Dependency & Composition); async session
    scoped to the call. Deps: step 14. Verification: `tests/integration/test_facts_persistence.py` against REAL
    Postgres (compose): insert → read back byte-identical domain object (str fields zero-tolerance, DOC-010 §
    Replay/DOC-013); insert same fact twice → one row. Complexity: moderate.

### Acquisition layer

17. **`src/onchain_platform/acquisition/providers/base.py`** — abstract async `BlockchainProvider` (ADR-006 §
    Provider Abstraction) exposing blockchain primitives ONLY, no business concepts (no `PairCreated` name inside
    providers — ADR-006 explicit): `get_chain_head() -> int`, `get_block_metadata(block_number) -> BlockMetadata`
    (number/hash/timestamp), `get_logs(from_block, to_block, address=None, topics=None) -> Sequence[RawLog]`.
    `RawLog`/`BlockMetadata` are typed interface primitives (frozen pydantic models) inside this file — provider
    shapes (`web3.LogReceipt`, raw JSON-RPC dicts) never cross this boundary (DOC-011 § What Does Not Belong Here).
    Every method abstract with documented timeout contract (DOC-013 § Async Conventions). Deps: step 9. Verification:
    mypy-strict clean; a no-op fake provider subclasses it in unit tests. Complexity: moderate.
18. **`src/onchain_platform/acquisition/providers/local_node.py`** — JSON-RPC/HTTPX implementation (methods:
    `eth_blockNumber`, `eth_getBlockByNumber`, `eth_getLogs`), explicit httpx timeout on every call (DOC-013),
    bounded retry-with-backoff for transient errors, all httpx exceptions translated to `AcquisitionError` before
    leaving the package (DOC-013 § Exception Hierarchy). RPC URL injected via constructor (no globals). Deliberately
    ignores non-standard extra fields some endpoints return in logs (e.g. `blockTimestamp` seen on the Base public
    endpoint) — block timestamp comes exclusively from `eth_getBlockByNumber`, one canonical path (ADR-006 Principle
    3; DOC-011: provider-specific shapes don't leak). Deps: step 17. Verification: unit tests with a stubbed httpx
    transport (permitted mock surface: provider abstraction, DOC-013 § Testing Conventions); then one real read-only
    call against `https://mainnet.base.org` behind the `live` marker. Complexity: moderate.
19. **`src/onchain_platform/acquisition/collector.py`** — polls the provider for new blocks on the configured chain,
    fetches `PairCreated` logs for ONE factory address + topic (config/constructor params), forwards each raw log +
    its block metadata to a callback (direct function call into processing — NO Redis Streams, ImplementationPlan §
    Milestone 1, and DOC-004's simple-over-sophisticated principle cited there). Constructor takes `clock:
    Callable[[], datetime]` — wall clock is read in `main.py` and injected; the Capability itself never calls
    `datetime.now()` (DOC-013 § Determinism Discipline — injection is the reading of that rule; logged §6 Q5).
    `observed_at` is stamped here at receipt using the injected clock (DOC-008: "time the external provider first
    observed the event" — at our RPC boundary); ordered iteration only (block order, then log_index — no set
    iteration, DOC-013). Graceful shutdown on SIGTERM/SIGINT finishes the in-flight block before exiting (DOC-013 §
    Async Conventions). Deps: step 18. Verification: unit test with fake provider asserting exact call sequence and
    emitted events for a scripted block sequence. Complexity: high.

### Processing layer

20. **`src/onchain_platform/processing/normalizer.py`** — `RawLog` + `BlockMetadata` → canonical intermediate shape:
    decodes the V2 `PairCreated` ABI (topics[1]=token0, topics[2]=token1, data word 0 = pair address; the trailing
    pair-index word is read but not a DOC-012 payload field), EIP-55-checksums all addresses (eth_utils), lowercases
    hashes, converts block timestamp → tz-aware UTC `event_time`. Raises `DomainValidationError` (not raw exceptions)
    on malformed input. Deps: steps 17, 11. Verification: unit tests against the exact captured sample log from the
    fixture probe (block 13,500,004 event, known token0=WETH `0x4200…0006`). Complexity: moderate.
21. **`src/onchain_platform/processing/fact_processor.py`** — canonical shape → `BlockchainFact` with
    `confirmation_status=PENDING`, `confirmations=0` (Finality Engine is Milestone 2 — out of scope), `fact_id`
    constructed as `f"{chain_id}:{tx_hash}:{log_index}"` (DOC-012 § B.1; `:` delimiter is safe for fact_id per
    DOC-012 § Composite ID Delimiter), `ingested_at` from injected clock, `schema_version="1.0"`, `payload.dex` from
    factory config. Enforces `fact_type` ↔ `payload.fact_type` sync (DOC-012 § Modeling note names this the
    processor's job). Logs with mandatory fields `chain_id`, `block_number`, `tx_hash` (DOC-013 § Observability).
    Deps: steps 20, 11. Verification: unit test producing the exact expected `BlockchainFact` for the fixture sample
    event (byte-identical str fields). Complexity: moderate.

### Composition + end-to-end tests

22. **`src/onchain_platform/main.py` + `platform/config.py` + `platform/logging.py`** — composition root wires:
    `Settings` (pydantic-settings, `.env`) → provider (local_node over `RPC_URL`) → collector → processor →
    repository; constructs the session factory and clock ONCE and threads them through (DOC-013 § Dependency &
    Composition); structlog configured once at startup (DOC-013 — logging is the documented exception to injection).
    `main.py` is wiring-only and contract-exempt (DOC-011 § Composition Root). Repoint `[project.scripts]`. Deps:
    steps 16, 19, 21. Verification: `uv run onchain-platform --help` style smoke; mypy/import-check still green
    (main.py is not named in any contract's source_modules — DOC-011). Complexity: moderate.
23. **`scripts/fetch_replay_fixture.py`** — dev tooling ONLY (DOC-011: scripts/ never production pipeline): pulls
    `eth_getLogs` + block headers for blocks 13,500,000–13,500,024 from the configured RPC, writes committed JSON
    (raw logs + headers) into `tests/replay/fixtures/base_pair_created_13500000_13500024.json`, plus a
    `FixtureProvider` (implements `BlockchainProvider` from the JSON). Fixture `observed_at`/`ingested_at` are pinned
    constants in the fixture file — replay determinism requires them fixed (ADR-006 Principle 2). Deps: step 17.
    Verification: fixture file committed; contains 5 events (assert in step 24). Complexity: trivial.
24. **`tests/replay/test_replay.py`** — FIRST REPLAY TEST: `FixtureProvider` → collector → normalizer → processor →
    (a) assert the 5 produced `BlockchainFact`s byte-identical against a committed baseline JSON (all fields str/enum
    → zero tolerance; pinned timestamps included); (b) run through the repository into a real Postgres, read back,
    assert row fields byte-identical to the baseline (DOC-010 § Testing; DOC-013 § Determinism — byte-identity is
    legitimate here because M1 fields are all str/int/enum; no float field is exercised yet). Deps: steps 21, 16, 23.
    Verification: `make test-replay` green. Complexity: moderate.
25. **`tests/integration/test_walking_skeleton.py`** — Collector → Fact Processor → Persistence against REAL
    Postgres with the FixtureProvider (integration = real infra, DOC-011/DOC-010; provider abstraction is the
    sanctioned seam): (a) real rows correct in every field (DoD 1, deterministic variant); (b) **idempotency: run
    the same block range twice → same rows, count unchanged** (DoD 2, ADR-006 § Idempotency — proven, not assumed);
    (c) kill/restart simulation out of scope (Milestone 2). Deps: step 22. Verification: `make test` green with
    compose services up. Complexity: moderate.
26. **Live smoke run (DoD 1, literal reading)** — `pytest -m live` test (and manual run): real collector against
    `https://mainnet.base.org` over a recent small block range on Base, asserting ≥0 rows inserted correctly and no
    exceptions; documents the one real event → real row moment. Deps: step 22. Verification: manual sign-off recorded
    in the milestone commit message. Complexity: trivial (but network-dependent — never gates CI).
27. **Final gate + commit** — `make lint && make typecheck && make import-check && make test && make test-replay`
    all green; update ImplementationPlan.md checkboxes for Milestone 1 DoD; commit.

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Public Base RPC rate-limits or rejects us during live runs (403 on missing User-Agent already observed during planning probes; `eth_getLogs` range limits on busy factories) | High | High | Fixture-driven tests need zero network (steps 23–24); provider abstraction makes Alchemy a config-level swap (`local_node.py` → `alchemy.py`, same interface, ADR-006 Principle 6); bounded retry+backoff inside provider translated to `AcquisitionError`; keep live ranges ≤ 25 blocks; UA header set explicitly. |
| PairCreated ABI differs across Base DEX factories | Medium (for future factories) | Medium | M1 uses ONE factory (ImplementationPlan). V2 `PairCreated(token0, token1, pair, uint)` is stable across V2 forks — verified live for the chosen address. Aerodrome's `PoolCreated` differs and is explicitly deferred; normalizer is written per-topic-signature, never per-DEX-assumption. |
| Pydantic discriminated union fails on real log data | Low–Medium | Medium | Schema tests (hypothesis) + unit tests on the exact captured real sample log before the replay test exists; DOC-012 mandates the discriminator pattern, so failures point at decoding, not the union design. |
| Import-linter blocks a necessary dependency | Low | Low | Contracts verified passing today on the empty tree. `processing` importing `acquisition` is layers-legal (acquisition is directly below processing). If a genuine need arises (none foreseen), that's a Document Resolution Protocol escalation, not a contract edit. |
| Replay fixture contains edge case not covered by schema | Medium | Medium | Range was probed live: 5 events, all standard V2 shape (incl. a WETH pair). Build step 23 re-fetches and step 24 asserts count=5; any decode failure is a blocker flagged with the offending log attached, per DOC-012 ("a schema gap is fixed in the doc, not patched locally"). |
| TimescaleDB extension not enabled in docker-compose | Medium | High | Compose verification is a Day-0 gate (step 3): `SELECT extversion FROM pg_extension…` must return a row before any migration work. Using the official `timescale/timescaledb` image where the extension ships preinstalled; migration additionally runs `CREATE EXTENSION IF NOT EXISTS`. |
| Alembic autogenerate cannot express PG ENUMs / generated columns / GIN indexes | High | Medium | Migration is hand-written (step 14) and verified against a FRESH container (`docker compose down -v && make run && make migrate`), not just an upgraded one. |
| `observed_at`/`ingested_at` semantics drift between live and replay | Medium | Medium | Both are injected-clock values (step 19); replay fixtures pin them as constants (step 23) — determinism is structural, documented in the fixture README. |
| EIP-55 checksum validation needs keccak; RPC returns lowercase addresses | High | Medium | Normalizer checksums addresses (eth_utils, Capability layer — allowed); schema validator checks checksummed form (DOC-012 § Conventions). Domain-layer use of eth_utils is the flagged assumption in §6 Q3. |
| Naive datetimes silently accepted by pydantic v2 | High (known pydantic behavior) | Medium | Explicit pure validator rejecting naive datetimes (step 11) with a dedicated failing-test-first unit test (DOC-012 § Conventions: validation error, not warning). |
| mypy --strict friction with httpx/SQLAlchemy typing at boundaries | Medium | Low | Cast/wrap into typed models at the provider boundary per DOC-013 § Determinism Discipline ("no Any in Capability interfaces"); budgeted into steps 18/16 complexity. |
| Solo-dev scope creep (second fact type, Redis, checkpoints "while we're here") | Medium | High | §5 out-of-scope list is enforced at review; ImplementationPlan § Milestone 1 scopes the full Confirmation Lifecycle OUT deliberately. |

---

## 4. Definition of Done Matrix

| DoD Item (ImplementationPlan § Milestone 1 + DOC-013 checklist) | Verification Method | Automated? |
|---|---|---|
| Real PairCreated on Base → real row, correct in every field | `tests/integration/test_walking_skeleton.py` (fixture → real Postgres, field-by-field assert) PLUS step 26 live smoke against real Base RPC | Integration: yes. Live smoke: manual/`-m live`, network-gated |
| Same block range twice → same row, no duplicate | Idempotency test: run pipeline twice on same DB; assert row count unchanged and rows byte-identical (ON CONFLICT DO NOTHING proven, ADR-006 § Idempotency) | Yes |
| First Replay Test exists and passes | `tests/replay/test_replay.py` + committed fixture + committed baseline JSON; `make test-replay` green; byte-identical comparison (all M1 fields are str/int/enum — no float field exists yet, so zero-tolerance is correct per DOC-010/DOC-013) | Yes |
| `lint-imports` still passes | `make import-check` in CI and local gate before commit | Yes |
| No `datetime.now()`/`time.time()` in Capability logic | grep gate in review + design: clock is constructor-injected everywhere (steps 19/21/22); ruff custom rule (flake8-bandit-style grep) added if cheap, else checklist item | Partially (grep + review) |
| Financial precision compliance | N/A for PairCreated (no Token Amount fields — intentional, ImplementationPlan § Why PairCreated first). Schema compliance verified instead: hypothesis tests assert all payload fields are `str`-typed addresses, no floats anywhere in `BlockchainFact`; `mypy --strict` catches type drift | Yes |
| All exceptions at boundaries are PlatformError subclasses | `local_node.py` wraps httpx→AcquisitionError; `repositories.py` wraps SQLAlchemy→PersistenceError; unit tests assert wrapped types; mypy + review | Partially (tests + review) |
| Log lines carry mandatory structured fields (DOC-013: acquisition/processing → chain_id, block_number, tx_hash) | structlog bound loggers at collector/processor entry points; integration test captures log output and asserts fields present (structlog testing processor) | Yes |
| Ruff + mypy pass | `make lint`, `make typecheck` (mypy strict) | Yes |
| Non-obvious logic cites document + section in comments | Review checklist (DOC-013 § Why With a Reference); enforced in spec-auditor pass | No (review) |
| Feature-name suffix / Decimal fields / no Settings-globals | N/A this milestone (no Features, no money, Settings threaded via constructor — verified by construction + import-check) | n/a |

---

## 5. Out-of-Scope Confirmation

Per ImplementationPlan § Milestone 1 ("Full Confirmation Lifecycle is not in scope yet — that's Milestone 2") and
§ What Not To Build Yet. Checked against every build step above:

- [x] Redis Streams — NOT introduced. Collector → Fact Processor is a direct function call (ImplementationPlan
      explicitly sanctions this for one fact type on one chain; `transport/` stays empty).
- [x] Finality Engine / CONFIRMED/FINALIZED transitions — NOT built. Facts persist as PENDING only;
      `processing/finality_engine.py` is not created.
- [x] Reorg handling / ORPHANED / ChainReorgEvent — NOT built (Milestone 2; DOC-012 § B.5 schema not typed yet).
- [x] Checkpointing — NOT built. No `checkpoints` table, no `acquisition/checkpoint.py`; collector start block is an
      explicit parameter. (DOC-012 § B.0 schema deferred with it.)
- [x] SwapExecuted / Liquidity facts — NOT processed. Payload classes exist only because DOC-012's discriminated
      union is one schema artifact; no collector topic, no decode path, no fixture for them.
- [x] Market Bars, Features, State Projection, Observation Snapshots — NOT built (`analytics/`, `transport/` empty).
- [x] Domain Management / Entity Resolution — NOT built (Tokens implied by PairCreated are NOT resolved into
      `tokens` rows — that is Milestone 4).
- [x] API endpoints (`research/api/`) and dashboard (`research/dashboard/`) — NOT built; DOC-015 untouched.
- [x] `config/confirmation_depth.yaml` — NOT created (Milestone 2).
- [x] No `utils/`/`common/` package; no UUID fact IDs; no offset pagination; no AI/RAG code; no second chain.

---

## 6. Questions / Blockers

Q1 (needs human, non-blocking): Confirm the DEX attribution of factory `0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6`
on Basescan so the payload `dex` string is correct (contract behavior is live-verified; its NAME is not). Fallback:
proceed with address-based collection and set `dex` from the confirmed attribution before the milestone commit.

Q2 (doc inconsistency, logged not guessed): ImplementationPlan § Milestone 1 says `domain/schemas/enums.py`; DOC-011's
tree shows `domain/enums.py`. Plan follows ImplementationPlan + task spec (two sources) for fact-lifecycle enums and
reserves `domain/enums.py` for ChainId/EntityType per DOC-011. If DOC-011 should be amended instead, say so before
step 10.

Q3 (flagged assumption): DOC-012 § Conventions makes EIP-55 checksumming "a schema-level validator", but keccak-256
is required and domain/ should stay free of crypto dependencies. Plan: normalizer (acquisition) checksums via
eth_utils; domain validator verifies checksummed form using eth_utils pure functions (no I/O — satisfies DOC-013
validator purity). If eth_utils in `domain/` is unacceptable, the alternative is format-only validation in domain
with checksum enforcement solely in the normalizer — needs a call.

Q4 (needs human): Do you have an Alchemy (or equivalent) API key? If yes, `.env.example`/CI can use it and
`alchemy.py` becomes a near-term step. If no, the plan's `local_node.py` + public Base endpoint path stands
(verified working today).

Q5 (design note, per Document Resolution Protocol — flagging rather than guessing silently): DOC-013 § Determinism
says wall-clock reads happen "only in main.py and platform/". A polling collector must stamp `observed_at` at data
entry. Plan resolves this by injecting a clock callable constructed in `main.py` — the Capability never touches the
clock itself, which is the strictest reading. If a looser reading was intended (acquisition may self-stamp at
ingestion), the injection design still satisfies it.

Q6 (needs human, CI infra): GitHub Actions workflow (step 7) — integration/replay jobs need Postgres/Redis service
containers. Confirm CI runs on GitHub (repo has an origin/master) so the workflow targets it; otherwise the gates
remain local-makefile-only for now.

No hard blockers: every Q above has a stated fallback that keeps Milestone 1 buildable today.
