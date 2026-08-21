# onchain_platform — Comprehensive Project Status Report
## Milestones 1–6 Complete

Repository: `HEAD 11ed1ac` | Branch: `master` | Date: 2026-08-20

---

## 1. Project Overview & Current State

### 1.1 Repository Statistics

| Metric | Value |
|---|---|
| Source `.py` files | 60 |
| Test `.py` files | 35 |
| Total git commits (M1–M6) | ~25 |
| Unit/integration/schema tests | 120 passing |
| Replay tests | 6 passing |
| Live smoke test | 1 passing |
| PostgreSQL tables | 13 (including alembic_version) |
| TimescaleDB hypertables | 3 (market_bars, observation_snapshots, features) |
| Redis keys used | `state:{chain_id}:{pool_address}` (StateProjection) |
| Import-linter contracts | 6 kept, 2 broken (known issue — see §6) |

### 1.2 Architecture Summary

The platform ingests blockchain events from EVM chains (Base first), normalizes them into canonical schemas, persists them as immutable facts, and derives analytical artifacts (Market Bars, Observation Snapshots, Features) from those facts. The entire pipeline is deterministic and replayable.

```
Blockchain RPC (Base)
    │
    ▼
Collector (acquisition/collector.py)
    │  polls for PairCreated, Swap, Mint, Burn logs
    ▼
Normalizer (processing/normalizer.py)
    │  ABI-decodes raw logs → canonical intermediate shapes
    ▼
Fact Processor (processing/fact_processor.py)
    │  canonical shape → BlockchainFact(PENDING)
    ▼
Finality Engine (processing/finality_engine.py)
    │  header buffer continuity check → CONFIRMED/FINALIZED/ORPHANED
    ▼
Persistence (persistence/postgres/repositories.py)
    │  blockchain_facts table (append-only, immutable once FINALIZED)
    ▼
Entity Resolution (domain_management/entity_resolution.py)
    │  PairCreated → Token + TradingPair + LiquidityPool + SmartContract
    │  SwapExecuted → Wallet
    ▼
Projection Engine (analytics/projection_engine.py)
    │  FINALIZED facts → StateProjection (Redis)
    ▼
Observation Snapshots (persistence/timescale/repositories.py)
    │  periodic snapshots of StateProjection → TimescaleDB
    ▼
Trade Aggregator (analytics/trade_aggregator.py)
    │  FINALIZED SwapExecuted → MarketBar OHLCV (TimescaleDB)
    ▼
Feature Engine (analytics/feature_engine.py)
    │  ObservationSnapshots + MarketBars → Features (TimescaleDB)
    ▼
APScheduler (platform/scheduler.py)
    hourly Feature computation for active pools
```

### 1.3 Technology Stack

**Runtime:** Python 3.12, asyncio throughout (DOC-010 § Runtime)

**Core dependencies** (from `pyproject.toml`):
- `pydantic` + `pydantic-settings` — Canonical Schemas, Settings
- `sqlalchemy[asyncio]` + `asyncpg` — ORM, async Postgres
- `alembic` — migrations
- `httpx[socks]` — async HTTP client for RPC calls
- `redis` — StateProjection cache
- `polars` — vectorized Feature computation
- `structlog` — structured JSON logging
- `web3` — installed but unused (provider abstraction uses raw JSON-RPC)
- `apscheduler` — hourly Feature computation scheduler
- `eth_utils` — EIP-55 address checksumming

**Infrastructure** (docker-compose.yml):
- `timescale/timescaledb:latest-pg16` — Postgres + TimescaleDB extension (host port 5433)
- `redis:7-alpine` — StateProjection cache (host port 6379)

### 1.4 Quality Gates

| Command | What it verifies |
|---|---|
| `make lint` | `ruff check .` + `ruff format --check .` — no lint errors, consistent formatting |
| `make typecheck` | `mypy src/ tests/` with `strict=true` — every function typed, no `Any` leaks |
| `make import-check` | 8 import-linter contracts (1 layers + 7 forbidden) — architectural boundaries enforced mechanically |
| `make test` | `pytest tests/unit tests/integration tests/schema -m "not live"` — 120 tests, excludes network-dependent live tests |
| `make test-replay` | `pytest tests/replay` — 6 replay tests, byte-identical (Decimal/str) or tolerance-based (float) |

---

## 2. Milestone-by-Milestone Breakdown

### 2.1 Milestone 1 — Walking Skeleton

**Goal:** One real `PairCreated` event on Base → one real row in `blockchain_facts` → idempotent replay produces identical row.

**Governing docs:** ImplementationPlan § Milestone 1, DOC-012 § B.1, ADR-006 § Single Processing Path.

**Files created:**
- `domain/schemas/blockchain_fact.py` — `BlockchainFact` + `PairCreatedPayload` (frozen Pydantic models, discriminated union, EIP-55 validators)
- `domain/schemas/enums.py` — `ConfirmationStatus`, `FactType` enums
- `domain/entities/blockchain.py` — `Blockchain` entity (avg_block_time_seconds is float — one of two sanctioned floats)
- `domain/exceptions.py` — `PlatformError` hierarchy (5 subclasses per DOC-013)
- `acquisition/providers/base.py` — `BlockchainProvider` ABC + `RawLog`/`BlockMetadata` primitives
- `acquisition/providers/local_node.py` — JSON-RPC over HTTPX with retry/backoff
- `acquisition/collector.py` — polling collector, direct function call to fact processor
- `processing/normalizer.py` — `normalize_pair_created()` ABI decoder
- `processing/fact_processor.py` — dispatches on `topics[0]`, builds `BlockchainFact(PENDING)`
- `persistence/postgres/facts.py` — `BlockchainFactRow` ORM (JSONB payload, native ENUMs, generated `involved_wallets` column)
- `persistence/postgres/repositories.py` — `save_fact()` with `ON CONFLICT DO NOTHING`
- `main.py` — composition root, wiring only
- `platform/config.py` — Pydantic Settings over `.env`
- `platform/logging.py` — structlog JSON + capability field enforcement

**Database:** `blockchain_facts` table (TEXT PK, BIGINT chain_id, native ENUMs, JSONB payload, TIMESTAMPTZ×3, CHECK confirmations>=0, GIN on involved_wallets, two B-tree indexes).

**Key algorithm:** The collector polls `eth_getLogs` with factory address + PairCreated topic, normalizes each log (ABI decode, EIP-55 checksum, lowercase hashes), constructs `fact_id = f"{chain_id}:{tx_hash}:{log_index}"`, persists via `ON CONFLICT DO NOTHING`.

**Tests:** 9 unit tests (pipeline pair_created), 4 integration tests (persistence), 1 replay test (5 real events from Base blocks 13,500,000–13,500,024, byte-identical on two passes), 1 live smoke test.

**Bugs fixed:** Hand-transcribed checksummed addresses in test factories had typos — caught by the schema validators themselves. Fixed by deriving all addresses through `eth_utils.to_checksum_address()`.

---

### 2.2 Milestone 2 — Finality & Reorg Handling

**Goal:** `BlockchainFact` correctly moves `PENDING → CONFIRMED → FINALIZED`; simulated reorg produces `ORPHANED`.

**Governing docs:** ADR-006 § Finality & Canonical Chain Validation Engine, DOC-013 § Immutability, DOC-012 § B.0 (Checkpoint), § B.5 (ChainReorgEvent).

**Files created:**
- `processing/finality_engine.py` — **highest correctness bar in the repository** (DOC-011). In-memory `deque[BlockMetadata]` header buffer (maxlen=confirmation_depth). On every new block: verify `parent_hash` continuity across the full confirmation window (not just the last block). Advance confirmations, finalize facts at depth, detect reorgs, orphan affected facts, advance checkpoint.
- `processing/reorg_handler.py` — `ReorgEventHandler` protocol + `LoggingReorgEventHandler` (INFO for shallow, WARNING for deep reorgs).
- `domain/schemas/checkpoint.py` — `Checkpoint` schema (DOC-012 § B.0).
- `domain/schemas/chain_reorg_event.py` — `ChainReorgEvent` schema (DOC-012 § B.5) with `|`-delimited composite ID.
- `config/confirmation_depth.yaml` — Base 3, Ethereum 12, BNB Chain 8.
- `acquisition/providers/base.py` — `BlockMetadata` gains `parent_hash` field.

**Database:** `checkpoints` table (mutable singleton per chain_id, PK chain_id, BIGINT last_finalized_block, TIMESTAMPTZ×2). Migration for `blockchain_facts` already existed from M1.

**Key algorithm — Finality Engine:**
```
on_new_block(block_number):
  1. Fetch block metadata (including parent_hash)
  2. Append to header buffer (deque, maxlen=confirmation_depth)
  3. If buffer has < 2 entries: return (filling)
  4. Verify continuity: for each i in [1, len(buffer)):
       buffer[i].parent_hash == buffer[i-1].hash
  5a. If continuous: advance_confirmation_counts(chain_id, head, depth)
      → SQL CASE: PENDING→CONFIRMED (>=1), CONFIRMED→FINALIZED (>=depth)
      → advance checkpoint to head - depth
  5b. If break at index i: fork_block = buffer[i-1]
      → mark_facts_orphaned(chain_id, fork+1, buffer[-1])
      → construct ChainReorgEvent, notify handler
      → pop divergent blocks from buffer
```

**Key algorithm — Immutability guard:** `mark_facts_orphaned` targets PENDING, CONFIRMED, AND FINALIZED facts in range (DOC-013: FINALIZED→ORPHANED is the one legal transition). `advance_confirmation_counts` excludes FINALIZED rows via WHERE clause.

**Tests:** 5 finality engine unit tests (lifecycle, single-block reorg, multi-block reorg, buffer-not-full, replay idempotency), 5 confirmation lifecycle integration tests, 3 reorg handling integration tests, 1 reorg replay test.

**Bugs fixed:**
1. `advance_confirmation_counts` produced negative confirmations when `head < block_number` → added `block_number <= current_chain_head` filter.
2. Buffer cleanup popped one too many blocks (`len - fork_index + 1` → `len - fork_index`).
3. `mark_facts_orphaned` excluded FINALIZED facts → added FINALIZED to the WHERE clause (DOC-013: the one legal transition).

---

### 2.3 Milestone 3 — SwapExecuted, Financial Precision, Market Bars

**Goal:** Real swaps become Decimal-precise facts; real OHLCV bar is correct and reproducible.

**Governing docs:** DOC-012 § B.1 (SwapExecutedPayload), § B.3 (MarketBar), DOC-008 § Financial Precision, DOC-014 § TimescaleDB Hypertables.

**Files created:**
- `processing/normalizer.py` — `SWAP_TOPIC`, `normalize_swap()` (V2 Swap ABI: sender/to indexed, 4×32-byte data words for amounts). All amounts as decimal strings, never float.
- `processing/fact_processor.py` — extended with `_process_swap()` dispatch.
- `domain/schemas/market_bar.py` — `MarketBar` schema (frozen, all OHLCV as str, `source_fact_range` tuple, `is_provisional`).
- `domain/schemas/enums.py` — `BarInterval` enum (1m/5m/15m/1h) with `.seconds` property.
- `persistence/timescale/repositories.py` — `MarketBarRow` ORM + `save_bar` (upsert), `list_bars`, `get_bar`.
- `analytics/trade_aggregator.py` — `aggregate_swaps_to_bar()`: Decimal math, price = token1 per token0, buy/sell volume (token0=base convention), VWAP = Σ(price×vol)/Σ(vol), epoch-based modulo bucketing.
- `scripts/fetch_replay_fixture.py` — fetches real Swap events from Base RPC.

**Database:** `market_bars` hypertable (TEXT interval, NUMERIC OHLCV, INTEGER trade_count, TIMESTAMPTZ, 7-day chunks, 30-day compression, `(pair_id, interval, bar_start_time DESC)` index). Composite PK `(bar_id, bar_start_time)` required by TimescaleDB.

**Key algorithm — Trade Aggregator:**
```
aggregate_swaps_to_bar(facts, pair_id, chain_id, interval, bar_start, computed_at):
  1. Sort facts by (block_number, log_index)
  2. For each swap:
     - If amount0_in > 0: price = amount1_out / amount0_in (token1 per token0)
       sell_volume += amount0_in
     - If amount1_in > 0: price = amount1_in / amount0_out (token1 per token0)
       buy_volume += amount0_out
     - Accumulate volume_base, volume_quote
  3. OHLCV: open=first price, high=max, low=min, close=last
  4. VWAP = Σ(price_i × vol_base_i) / Σ(vol_base_i)
  5. source_fact_range = (first.fact_id, last.fact_id)
```

**Tests:** 8 trade aggregator unit tests, 2 market bars integration tests (OHLCV correctness, bar recomputation on reorg), 1 swap replay test (5 real Swap events from 5 pools, byte-identical on two passes).

**Bugs fixed:**
1. `Decimal('5000') / Decimal('1000')` = `Decimal('5')`, not `"5.0"` — test expectations adjusted to natural Decimal representation.
2. `market_bars.interval` column: SQLAlchemy `Enum(BarInterval, native_enum=True)` sent Python enum name ("ONE_MINUTE") instead of value ("1m") → changed to `Text` column, send `bar.interval.value` in values dict.

---

### 2.4 Milestone 4 — Domain Management

**Goal:** Token, TradingPair, LiquidityPool exist as real, queryable entities with stable Canonical IDs.

**Governing docs:** DOC-012 Part A (Entity Schemas), DOC-014 § Storage Assignment, DOC-014 § Data Integrity Constraints.

**Files created:**
- `domain/ids.py` — Canonical ID construction: `token_canonical_id(chain_id, address)`, `pair_canonical_id(chain_id, pool_address)`, `wallet_canonical_id(chain_id, address)`, `smart_contract_canonical_id(chain_id, address)`. All EIP-55 checksummed.
- `domain/enums.py` — `ContractType` (ERC20/FACTORY/ROUTER/POOL/UNKNOWN), `VerificationStatus` (UNVERIFIED/PENDING/VERIFIED).
- `domain/entities/token.py`, `trading_pair.py`, `liquidity_pool.py`, `wallet.py`, `smart_contract.py`, `metadata.py` — frozen Pydantic models per DOC-012 Part A.
- `persistence/postgres/models.py` — ORM models for all Part A entities. FK: `trading_pairs.base_token_id` → `tokens.canonical_id` (monomorphic, DOC-014).
- `persistence/postgres/entity_repositories.py` — CRUD with idempotent upserts. `save_wallet` uses `func.least()` for `first_seen_at` (only update if earlier).
- `domain_management/entity_resolution.py` — eager, synchronous resolution on fact ingestion. `resolve_from_pair_created`: creates SmartContract×3, Token×2, TradingPair, LiquidityPool. `resolve_from_swap_executed`: creates Wallet for sender/recipient.
- `domain_management/wallet_service.py` — minimal Wallet lifecycle.
- `domain_management/metadata_service.py` — stub returning UNVERIFIED.

**Database:** 6 new tables: `tokens` (TEXT PK, VARCHAR(42) contract_address UNIQUE, NUMERIC(78,0) total_supply), `trading_pairs` (TEXT PK, FK to tokens, VARCHAR(42) pool_address UNIQUE, two indexes on base/quote_token_id), `liquidity_pools` (TEXT PK FK to trading_pairs), `wallets` (TEXT PK, VARCHAR(42) address UNIQUE), `smart_contracts` (TEXT PK, VARCHAR(42) address), `metadata` (TEXT PK entity_id, JSONB social_links).

**Key design decision:** Entity resolution is synchronous within the fact processor handler (DOC-004 simplicity principle), not a separate async pipeline. The handler in main.py calls entity resolution in the same session as fact persistence — atomic, no partial state.

**Tests:** 5 entity resolution unit tests (PairCreated creates entities, SwapExecuted creates wallets, idempotent, wallet first_seen_at not overwritten, list_pairs_for_token).

---

### 2.5 Milestone 5 — State Projection & Observation Snapshots

**Goal:** "What does this pool look like right now?" (Redis) and "What did it look like at 14:32 on Tuesday?" (TimescaleDB) both answer correctly.

**Governing docs:** DOC-012 § B.2 (StateProjection), § B.3 (ObservationSnapshot), DOC-006 § Data Lifecycle.

**Files created:**
- `domain/schemas/state_projection.py` — `StateProjection` schema (frozen, reserve0/reserve1 as Token Amount strings, price as Decimal-as-string).
- `domain/schemas/observation_snapshot.py` — `ObservationSnapshot` schema (snapshot_id with `|` delimiter, all M5 nullable fields set to None).
- `transport/state_cache.py` — Redis-backed store: `save_state`, `load_state`, `delete_state`, `list_state_keys`. JSON serialization via Pydantic `.model_dump_json()`. Key: `state:{chain_id}:{pool_address}`.
- `persistence/timescale/repositories.py` — `ObservationSnapshotRow` + CRUD (upsert on composite PK `(snapshot_id, snapshot_timestamp)`).
- `analytics/projection_engine.py` — consumes FINALIZED SWAP_EXECUTED, LIQUIDITY_ADDED, LIQUIDITY_REMOVED facts; updates StateProjection in Redis. All intermediate math uses Decimal. Price = reserve1/reserve0 (token1 per token0). `rebuild_from_facts()` replays all FINALIZED facts to restore state on startup (DOC-006: "State can always be reconstructed by replaying Facts").
- `processing/normalizer.py` — `MINT_TOPIC`, `BURN_TOPIC`, `normalize_liquidity()` for V2 Mint/Burn events.
- `processing/fact_processor.py` — `_process_liquidity()` dispatches Mint→LIQUIDITY_ADDED, Burn→LIQUIDITY_REMOVED.

**Database:** `observation_snapshots` hypertable (1-day chunks, 7-day compression, `(entity_id, snapshot_timestamp DESC)` PIT index).

**Key algorithm — Projection Engine:**
```
update_projection(session, redis_client, fact, clock):
  1. Extract pool_address from fact payload
  2. Load current StateProjection from Redis (or create zero-state)
  3. Look up TradingPair for token ordering
  4. Apply fact to reserves:
     - SWAP_EXECUTED: r0 += amount0_in - amount0_out, r1 += amount1_in - amount1_out
     - LIQUIDITY_ADDED: r0 += amount0, r1 += amount1
     - LIQUIDITY_REMOVED: r0 -= amount0, r1 -= amount1
  5. Clamp reserves to >= 0 (defensive)
  6. Recompute price = reserve1 / reserve0
  7. Save updated StateProjection to Redis
```

**Tests:** 7 state schema tests, 4 projection engine unit tests, 3 state projection integration tests, 3 observation snapshot integration tests, 1 replay test extension.

---

### 2.6 Milestone 6 — Feature Engineering

**Goal:** First two real Features, Polars-backed, Point-in-Time correct.

**Governing docs:** DOC-012 § B.3 (Feature schema), § Feature Naming Convention, DOC-013 § Determinism Discipline, DOC-008 § Point-in-Time Correctness.

**Files created:**
- `domain/schemas/feature.py` — `Feature` schema (frozen, `value: float` — first genuine float field, `feature_name` naming-convention validator, `inputs: list[str]` non-empty per DOC-012 Traceability Chain).
- `domain/schemas/enums.py` — `EntityType` enum (TRADING_PAIR, WALLET, TOKEN).
- `persistence/timescale/repositories.py` — `FeatureRow` (DOUBLE PRECISION for value), `save_feature` (upsert), `get_feature_at` (PIT query: most recent with `as_of_timestamp <= as_of ORDER BY DESC LIMIT 1`), `list_features`.
- `analytics/feature_engine.py` — `compute_liquidity_growth_pct_1h` (Decimal intermediate, float output) and `compute_price_momentum_zscore_1h` (Polars DataFrame, z-score with std=0 edge case returning 0.0). Both PIT-filtered, inputs populated.
- `platform/scheduler.py` — APScheduler hourly job computing features for all active pools (those with StateProjection in Redis).

**Database:** `features` hypertable (DOUBLE PRECISION value, TEXT[] inputs, 1-day chunks, 7-day compression, `(entity_id, feature_name, as_of_timestamp)` PIT query index).

**Key design decisions:**
1. `Feature.value` is `float` — the first genuine float in the platform (DOC-012 § Conventions clarification). All other financial fields remain Decimal/str. Intermediate computation uses Decimal; only the final output relaxes to float.
2. Replay tests use tolerance `1e-10` for `Feature.value` (DOC-013 § Determinism Discipline: Polars multi-threaded aggregation doesn't guarantee fixed float accumulation order).
3. Feature naming convention enforced by validator: `feature_name` must contain one of `_pct`, `_ratio`, `_score`, `_zscore`, `_usd`, `_delta`.
4. PIT query: `get_feature_at(session, entity_id, feature_name, as_of)` — single code path for both backtest (past timestamp) and live (now) queries. This is the Definition of Done.

**Tests:** 7 feature schema tests, 6 feature engine unit tests, 4 feature engine integration tests.

---

## 3. Cross-Cutting Concerns

### 3.1 Data Flow — Complete Lifecycle

| Stage | Canonical Schema | Validation | Storage | Tests |
|---|---|---|---|---|
| Collector polls `eth_getLogs` | `RawLog` (acquisition primitive) | Lowercase hex, sorted by (block, log_index) | In-memory only | Unit: FakeProvider |
| Normalizer ABI-decodes | `NormalizedPairCreatedEvent` / `NormalizedSwapEvent` / `NormalizedLiquidityEvent` | EIP-55 checksum, removed-log rejection, topic validation | In-memory only | Unit: real captured logs |
| Fact Processor builds fact | `BlockchainFact` (PENDING) | Pydantic validators: tz-aware timestamps, fact_id consistency, frozen model | `blockchain_facts` (INSERT ON CONFLICT DO NOTHING) | Unit + integration |
| Finality Engine advances | `BlockchainFact` (CONFIRMED→FINALIZED or ORPHANED) | Header buffer continuity, confirmation depth, immutability guard | `blockchain_facts` UPDATE + `checkpoints` upsert | Unit + integration |
| Entity Resolution | `Token`, `TradingPair`, `LiquidityPool`, `Wallet`, `SmartContract` | Canonical ID format, FK integrity | Part A tables (upsert) | Unit + integration |
| Projection Engine | `StateProjection` | Decimal reserves, token1/token0 ordering | Redis (JSON) | Unit + integration |
| Observation Snapshots | `ObservationSnapshot` | snapshot_id composite key, tz-aware | `observation_snapshots` hypertable | Integration |
| Trade Aggregator | `MarketBar` | Decimal OHLCV, source_fact_range, is_provisional | `market_bars` hypertable | Unit + integration |
| Feature Engine | `Feature` | Naming convention suffix, non-empty inputs, PIT filter | `features` hypertable | Unit + integration |

### 3.2 Immutability & Reproducibility

**Blockchain Facts are append-only:** `blockchain_facts` rows are INSERT-only (ON CONFLICT DO NOTHING). The sole legal mutation is the FINALIZED→ORPHANED transition via `mark_facts_orphaned`. The `advance_confirmation_counts` WHERE clause excludes FINALIZED rows. No UPDATE path exists for FINALIZED→anything else.

**Replay Tests are deterministic:** M1–M3 facts are all str/int/enum → byte-identical zero-tolerance. M6 introduces `Feature.value` as float → tolerance `1e-10`. Polars runs multi-threaded (default); `POLARS_MAX_THREADS=1` is never set. No `set` iteration on aggregation paths.

**Point-in-Time correctness:** `get_feature_at(session, entity_id, feature_name, as_of)` filters `as_of_timestamp <= as_of ORDER BY DESC LIMIT 1`. The same function serves backtest (past timestamp) and live (now) queries. No lookahead bias.

**Reorg handling:** Finality engine detects continuity breaks in the header buffer → marks affected facts ORPHANED → `ChainReorgEvent` constructed and logged (Redis publishing deferred). Checkpoint only advances on finalization. On restart, state is rebuilt from checkpoint forward.

### 3.3 Financial Precision

**Decimal for all on-chain quantities:** Token amounts (`reserve0`, `reserve1`, `amount0_in`, etc.) are `str`-typed `Decimal` from the moment they're parsed. Never `float`, never native JSON number. DB column type: `NUMERIC(78,0)` for uint256, unconstrained `NUMERIC` for prices/ratios.

**float only for Feature.value:** DOC-012 § Conventions clarification: "any field genuinely computed by the Feature Engine from one or more Decimal inputs is float in the Feature schema." Intermediate computation uses Decimal; only the final output relaxes. DB column: `DOUBLE PRECISION`.

**VWAP, price, OHLCV:** All Decimal-as-string. `price = reserve1 / reserve0` (token1 per token0). `vwap = Σ(price × volume_base) / Σ(volume_base)`. All intermediate math in Python `Decimal` with precision 78.

### 3.4 Domain Model & Entity Resolution

**Structural entities** (DOC-012 Part A, `domain/entities/`): Token, TradingPair, LiquidityPool, Wallet, SmartContract, Metadata. Slowly-changing registry objects. Stable Canonical IDs: `eip155:<chain_id>/<type>:<address>`. Stored in PostgreSQL.

**Temporal schemas** (DOC-012 Part B, `domain/schemas/`): BlockchainFact (B.1), Checkpoint (B.0), StateProjection (B.2), ObservationSnapshot (B.3), MarketBar (B.3), Feature (B.3), ChainReorgEvent (B.5). Append-only or mutable-singleton. Stored in PostgreSQL/TimescaleDB/Redis.

**Canonical ID stability:** Addresses are immutable on-chain — reorgs change block history, not contract addresses. Canonical IDs are stable across reorgs. Composite IDs use `|` delimiter (not `:`) because Canonical IDs and ISO timestamps both contain `:` (DOC-012 § Composite ID Delimiter).

---

## 4. Database Schema Overview

### 4.1 PostgreSQL Tables (Operational)

| Table | Purpose | PK | Key columns | Indexes |
|---|---|---|---|---|
| `blockchain_facts` | Append-only blockchain history | `fact_id` TEXT | chain_id BIGINT, fact_type ENUM, block_number BIGINT, payload JSONB, involved_wallets TEXT[] GENERATED | (chain_id, confirmation_status), (chain_id, block_number), GIN(involved_wallets) |
| `checkpoints` | Ingestion progress per chain | `chain_id` BIGINT | last_finalized_block BIGINT, last_finalized_at TIMESTAMPTZ | — |
| `blockchains` | Chain registry | `chain_id` BIGINT | name, native_asset_symbol, avg_block_time_seconds DOUBLE PRECISION | — |
| `tokens` | Token entities | `canonical_id` TEXT | contract_address VARCHAR(42) UNIQUE, symbol, decimals, total_supply NUMERIC(78,0) | (chain_id) |
| `trading_pairs` | Pair entities | `canonical_id` TEXT | base_token_id FK→tokens, quote_token_id FK→tokens, pool_address VARCHAR(42) UNIQUE | (base_token_id), (quote_token_id), (chain_id) |
| `liquidity_pools` | Pool config | `canonical_id` TEXT FK→trading_pairs | protocol, fee_tier_bps | — |
| `wallets` | Wallet entities | `canonical_id` TEXT | address VARCHAR(42) UNIQUE, first_seen_at TIMESTAMPTZ | (chain_id) |
| `smart_contracts` | Contract registry | `canonical_id` TEXT | address VARCHAR(42), contract_type ENUM | (address), (chain_id) |
| `metadata` | Entity enrichment | `entity_id` TEXT | social_links JSONB, verification_status ENUM | — |

### 4.2 TimescaleDB Hypertables (Analytical)

| Hypertable | Partitioning | Chunk interval | Compression | Index |
|---|---|---|---|---|
| `market_bars` | `bar_start_time` | 7 days | >30 days | (pair_id, interval, bar_start_time DESC) |
| `observation_snapshots` | `snapshot_timestamp` | 1 day | >7 days | (entity_id, snapshot_timestamp DESC) |
| `features` | `as_of_timestamp` | 1 day | >7 days | (entity_id, feature_name, as_of_timestamp) |

### 4.3 Redis Usage

| Key pattern | Schema | Purpose |
|---|---|---|
| `state:{chain_id}:{pool_address}` | `StateProjection` JSON | Live pool reserves/price, rebuilt from facts on restart |

---

## 5. Testing Strategy & Quality Gates

### 5.1 Test Categories

| Category | Directory | Count | What it verifies |
|---|---|---|---|
| Unit | `tests/unit/` | ~80 | Schema validation, pure functions, normalizer decode, fact processor dispatch, projection engine math, feature engine math, trade aggregator OHLCV |
| Integration | `tests/integration/` | ~15 | Real Postgres/TimescaleDB: persistence round-trip, confirmation lifecycle, reorg handling, entity resolution, state projection, observation snapshots, feature computation, PIT query |
| Schema | `tests/schema/` | ~20 | Hypothesis property-based tests for all Canonical Schemas (round-trip, frozen rejection, validator enforcement) |
| Replay | `tests/replay/` | 6 | Byte-identical (Decimal/str) or tolerance-based (float) reproducibility across two passes of the same fixture |
| Live smoke | `tests/integration/test_live_smoke.py` | 1 | Real Base RPC → real row, network-gated (`-m live`) |

### 5.2 Replay Test Fixtures

| Fixture | Block range | Content | Scenarios |
|---|---|---|---|
| `base_pair_created_13500000_13500024.json` | 13,500,000–13,500,024 | 5 PairCreated + 5 Swap events from 5 pools, 25 block headers with parentHash | Canonical chain, byte-identical replay, idempotent persistence |
| `reorg_simulator.py` | Synthetic | `ReorgSimulatorProvider` serving canonical then divergent chain | Single-block reorg detection, ORPHANED marking |

### 5.3 Quality Gate Commands

- `make lint` → `ruff check . && ruff format --check .` — catches style violations, unused imports, line length
- `make typecheck` → `mypy src/ tests/` with `strict=true` — catches type errors, missing annotations, union narrowing
- `make import-check` → `lint-imports` — 8 contracts (1 layers + 7 forbidden) enforce DOC-011 dependency graph
- `make test` → `pytest tests/unit tests/integration tests/schema -m "not live"` — all fast tests
- `make test-replay` → `pytest tests/replay` — reproducibility tests (separate because they need fixture data)

---

## 6. Known Limitations & Technical Debt

### 6.1 Import-Linter Violations (2 broken contracts)

**Issue:** `acquisition/collector.py` imports `processing/finality_engine.py` (capability importing upward), and `platform/scheduler.py` imports `analytics/feature_engine.py` (cross-cutting importing capability).

**Root cause:** The collector calls `finality_engine.on_new_block()` directly (M2 design decision). The scheduler calls `feature_engine.compute_*()` directly (M6 design decision).

**Fix:** Refactor collector to accept an `on_block_processed` callback (wired by main.py to the finality engine). Move scheduler's feature computation logic into main.py or a thin orchestrator. This restores the architectural boundaries.

**Priority:** Must fix before M7.

### 6.2 market_bars.interval Column Type

**Issue:** Changed from native ENUM to TEXT to work around SQLAlchemy StrEnum/native_enum interaction. The DB-level ENUM constraint is lost.

**Fix:** Investigate SQLAlchemy's `values_callable` parameter or upgrade to a version with better StrEnum support. Low priority — Pydantic validates the value before persistence.

### 6.3 No Uniswap V3 Support

**Issue:** All normalizers decode V2 event signatures only. V3 Swap, Mint, Burn have different ABIs.

**Impact:** Only V2-style pools are processed. Aerodrome (Solidly-fork) uses a different Swap signature.

**Fix:** Deferred per DOC-012 § Known future extension.

### 6.4 No Multi-Chain Ingestion

**Issue:** Only Base is actively ingested. Ethereum and BNB Chain have confirmation depths configured but no active collectors.

**Fix:** Deferred to later milestones. Architecture supports it (per-chain config, independent checkpoints).

### 6.5 Stub Metadata

**Issue:** `metadata_service.py` returns UNVERIFIED for everything. No real token metadata (symbol, name, decimals) is fetched.

**Impact:** Token entities have placeholder values (symbol="UNKNOWN", decimals=18).

**Fix:** Deferred to a future milestone with a real metadata provider.

### 6.6 No Holder Count / Market Cap / FDV

**Issue:** `ObservationSnapshot.holder_count`, `market_cap_usd`, `fdv_usd` are all None. These require external data (token transfer events, price oracles).

**Fix:** Deferred.

### 6.7 No API Endpoints

**Issue:** `research/api/` is empty. No REST API for querying data.

**Fix:** Milestone 9.

---

## 7. Readiness for Milestone 7 (Intelligence — Basic Risk Analysis)

### 7.1 What Exists

- `intelligence/` package exists (empty `__init__.py`)
- `intelligence/risk_rules.py` is the target file per DOC-011
- HTTPX is available for external API calls (DOC-010 § Blockchain Connectivity)
- `domain_management/entity_resolution.py` resolves entities — risk analysis can query them
- `analytics/feature_engine.py` computes Features — risk analysis can consume them
- `platform/config.py` can hold API keys for external risk services (GoPlus, etc.)

### 7.2 What Needs Building

- `intelligence/risk_rules.py` — deterministic risk rules consuming external commodity data (GoPlus or equivalent)
- External API client for risk scoring (HTTPX with explicit timeout, DOC-013 § Async Conventions)
- Risk score stored as a Feature or a new schema (DOC-012 § B.3 Feature vs DOC-009 Intelligence)
- Integration with entity resolution (risk score per TradingPair)

### 7.3 Risks & Blockers

- **Import-linter violations** must be fixed first (§6.1)
- **External API reliability** — risk API may be down; need graceful degradation
- **Rate limits** — free tier may throttle; need backoff
- **Risk score schema** — DOC-012 doesn't have a dedicated RiskScore schema; may use Feature with a risk-specific suffix

### 7.4 Open Decisions

- Which risk API? (GoPlus, DexScreener, etc.)
- Risk score stored as Feature or new schema?
- How to handle API failures (skip, retry, cache)?

---

## 8. Key Invariants & Constraints

These rules are non-negotiable. Violating any of them is a critical failure.

| Invariant | Enforcement | Reference |
|---|---|---|
| `domain/` imports nothing else in this repo | import-linter (8 contracts) | DOC-011, AGENTS.md |
| Money is `Decimal`/`str`, never `float` (except `Feature.value` and `avg_block_time_seconds`) | mypy strict + schema validators | DOC-008, DOC-012 |
| FINALIZED rows are immutable (sole exception: FINALIZED→ORPHANED) | `mark_facts_orphaned` WHERE clause | DOC-013, DOC-014 |
| No `datetime.now()` inside Capability logic | grep gate + design (clock injected) | DOC-013 |
| Canonical Schemas are the only cross-boundary contract | import-linter + code review | DOC-012, DOC-015 |
| Reorgs are `ChainReorgEvent` on Redis Streams, never Python exceptions | `reorg_handler.py` protocol | DOC-012 § B.5, DOC-013 |
| Infrastructure exceptions translated to `PlatformError` at boundaries | `domain/exceptions.py` hierarchy | DOC-013 |
| Single processing path for live and historical data | `collector.process_range()` used by both | ADR-006 |
| Analytics consume only FINALIZED facts | `trade_aggregator.py`, `projection_engine.py` filter on FINALIZED | ADR-006, DOC-012 |
| Feature names carry unit suffix | `_pct`, `_ratio`, `_score`, `_zscore`, `_usd`, `_delta` validator | DOC-012 § Feature Naming |
| Replay tests: byte-identical for str/int/enum, tolerance 1e-10 for float | `test_replay*.py` assertions | DOC-010, DOC-013 |
| No `set` iteration on aggregation paths | code review + ruff ASYNC rules | DOC-013 |
| Polars runs multi-threaded (default) | never set `POLARS_MAX_THREADS=1` | DOC-013 |
| `inputs` list on Feature must be non-empty | Pydantic `min_length=1` validator | DOC-012 § Traceability Chain |
