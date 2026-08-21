# Milestone 5 Execution Plan — State Projection & Observation Snapshots

Status: Planning artifact. Implements `docs/implementation/ImplementationPlan.md` § Milestone 5.
Prepared: 2026-08-20, after re-reading ImplementationPlan § Milestone 5, DOC-012 § B.2
(StateProjection), DOC-012 § B.3 (ObservationSnapshot), DOC-014 § TimescaleDB Hypertables /
§ Indexing Strategy, DOC-006 § Data Lifecycle, DOC-007 § Data Flow.

Goal (verbatim from ImplementationPlan): "what does this pool look like right now" (Redis,
StateProjection) and "what did it look like at 14:32 on Tuesday" (TimescaleDB,
ObservationSnapshot) both answer correctly.

---

## 0. Pre-Flight Status

Verified against HEAD 8c67c1c (commands run, not assumed):

| Item | Status | Detail |
|---|---|---|
| M4 gates pass | ✅ Done | ruff clean, mypy strict 80 files 0 issues, lint-imports 8/8, 77 fast + 5 replay + 1 live smoke |
| `analytics/projection_engine.py` does NOT exist | ✅ Confirmed | `analytics/` contains only `trade_aggregator.py` and `__init__.py`. |
| `transport/state_cache.py` does NOT exist | ✅ Confirmed | `transport/` contains only `__init__.py`. |
| `persistence/timescale/repositories.py` has only market_bars | ✅ Confirmed | Only `MarketBarRow` and related CRUD functions. |
| `domain/schemas/state_projection.py` does NOT exist | ❌ Needs creation | Not in `domain/schemas/`. Must be created (DOC-012 § B.2). |
| `domain/schemas/observation_snapshot.py` does NOT exist | ❌ Needs creation | Not in `domain/schemas/`. Must be created (DOC-012 § B.3). |
| Redis is in docker-compose.yml | ✅ Confirmed | 4 matches — redis service defined, port 6379 exposed. |
| `persistence/postgres/facts.py` has blockchain_facts + checkpoints | ✅ Confirmed | `BlockchainFactRow`, `CheckpointRow`, `BlockchainRow` classes. |
| `trade_aggregator.py` queries FINALIZED facts | ✅ Confirmed | Uses `aggregate_swaps_to_bar()` with Decimal math — pattern to follow. |

**Pre-flight summary:** M4 artifacts in place, M5 prerequisites absent. The StateProjection and
ObservationSnapshot domain schemas, projection engine, Redis cache layer, snapshot hypertable,
and snapshot scheduler all need to be created from scratch.

---

## 1. Open Decisions — Resolved

| Decision | Resolution | Rationale |
|---|---|---|
| State Projection update trigger | **Eager, on every finalized fact that affects a pool's reserves.** When a SWAP_EXECUTED, LIQUIDITY_ADDED, or LIQUIDITY_REMOVED fact reaches FINALIZED status, the projection engine recomputes the affected pool's StateProjection. Not lazy (too slow for dashboards), not scheduled (too coarse). | DOC-006 § Data Lifecycle: "State is continuously updated from Blockchain Facts." DOC-012 § B.2: "The live, mutable, continuously-recomputed read model." Eager update ensures Redis always reflects the latest finalized state. |
| Redis serialization format | **JSON via Pydantic `.model_dump_json()`** with `schema_version` field. Key format: `state:{chain_id}:{pool_address}` (human-readable, debuggable). TTL: none (state is rebuilt from facts on restart, but keeping it in Redis avoids cold-start latency). | DOC-012 § B.2: StateProjection is "served from Redis cache." JSON is agent-readable (DOC-010 § AI-friendly), schema_version enables future migration (DOC-012 § Schema Versioning Policy). |
| Observation Snapshot frequency | **Configurable per entity type, default 1 minute for active pools.** The snapshot scheduler runs every N seconds (configurable via Settings), reads current StateProjection from Redis, writes ObservationSnapshot to TimescaleDB. Skip if no new facts since last snapshot (no-op optimization). | DOC-012 § B.3: ObservationSnapshot is "the historically-preserved recording of State." 1-minute default balances storage cost vs. research resolution. Configurable because different research use cases need different granularity. |
| Snapshot storage strategy | **Upsert on `(entity_id, snapshot_timestamp, source)` composite key.** If a snapshot is taken at the same timestamp twice (e.g., restart), the latest values overwrite. DOC-012 § B.3: `snapshot_id = f"{entity_id}\|{snapshot_timestamp.isoformat()}\|{source}"` — the composite key includes `source` so two sources snapshotting the same entity at the same instant never collide. | DOC-012 § B.3 defines the composite key. Upsert matches the "latest wins" semantics for restart scenarios. |
| State reconstruction on startup | **Replay from last snapshot timestamp forward.** On startup, the projection engine: (1) loads the latest ObservationSnapshot for each entity from TimescaleDB, (2) replays all FINALIZED facts from that snapshot's timestamp forward, (3) writes the resulting StateProjection to Redis. Fallback: if no snapshots exist, replay all FINALIZED facts from genesis. | DOC-006: "State can always be reconstructed by replaying Facts." Starting from the last snapshot is faster than full replay. The fallback ensures correctness even without snapshots. |

---

## 2. Build Order (Sequential)

Gates: every step passes `make lint && make typecheck && make import-check` before the next begins.

### Phase A: Domain Layer (schemas)

1. **`src/onchain_platform/domain/schemas/state_projection.py`** — StateProjection schema (DOC-012 § B.2).
   - Frozen Pydantic model. Fields: `schema_version`, `entity_id` (Canonical ID of LiquidityPool), `chain_id`, `as_of_block`, `as_of_fact_id`, `computed_at`, `reserve0` (str — Token Amount), `reserve1` (str), `price` (str — token1 per token0, Decimal-as-string).
   - Validator: `reserve0` and `reserve1` are non-negative integer strings (Token Amount, DOC-008).
   - Deps: none.
   - Verification: unit test round-trip, frozen-mutation rejection.
   - Complexity: trivial.

2. **`src/onchain_platform/domain/schemas/observation_snapshot.py`** — ObservationSnapshot schema (DOC-012 § B.3).
   - Frozen Pydantic model. Fields: `schema_version`, `snapshot_id` (`f"{entity_id}|{snapshot_timestamp.isoformat()}|{source}"` — `|` delimiter per DOC-012 § Composite ID Delimiter), `entity_id`, `chain_id`, `snapshot_timestamp`, `observed_at`, `ingested_at`, `source` (str, e.g. `"projection_engine:poll:60s"`), `snapshot_version` (int), `reserve0`, `reserve1`, `price`, `liquidity_usd` (str | None), `holder_count` (int | None), `market_cap_usd` (str | None), `fdv_usd` (str | None).
   - M5 scope: `liquidity_usd`, `holder_count`, `market_cap_usd`, `fdv_usd` are all None (require external price oracle / token transfer events — deferred).
   - Deps: none.
   - Verification: unit test round-trip, snapshot_id format validation.
   - Complexity: trivial.

3. **`tests/unit/test_state_schemas.py`** — Unit tests for StateProjection + ObservationSnapshot.
   - Round-trip tests, frozen-mutation rejection, snapshot_id format validation, reserve validation.
   - Deps: steps 1, 2.
   - Verification: `make test` green.
   - Complexity: trivial.

### Phase B: Transport Layer (Redis cache)

4. **`src/onchain_platform/transport/state_cache.py`** — Redis-backed StateProjection store (DOC-012 § B.2).
   - `save_state(redis_client, projection: StateProjection) -> None` — serializes via `.model_dump_json()`, stores at key `state:{chain_id}:{pool_address}`.
   - `load_state(redis_client, chain_id: int, pool_address: str) -> StateProjection | None` — deserializes from Redis, returns None if key doesn't exist.
   - `delete_state(redis_client, chain_id: int, pool_address: str) -> None` — removes key (used on reorg).
   - All Redis errors → `TransportError` (DOC-013 § Exception Hierarchy).
   - Deps: step 1.
   - Verification: unit test with fakeredis (or mock).
   - Complexity: moderate.

### Phase C: Persistence Layer (ObservationSnapshot hypertable)

5. **`src/onchain_platform/persistence/timescale/repositories.py`** — add `ObservationSnapshotRow` ORM model + CRUD.
   - Column types per DOC-014: `snapshot_id` TEXT PK, `entity_id` TEXT, `chain_id` BIGINT, `snapshot_timestamp` TIMESTAMPTZ, `observed_at`/`ingested_at` TIMESTAMPTZ, `source` TEXT, `snapshot_version` INTEGER, `reserve0`/`reserve1`/`price` NUMERIC, `liquidity_usd`/`market_cap_usd`/`fdv_usd` NUMERIC (nullable), `holder_count` INTEGER (nullable).
   - Upsert on `(entity_id, snapshot_timestamp, source)` composite key.
   - `save_snapshot(session, snapshot: ObservationSnapshot) -> bool`
   - `get_latest_snapshot(session, entity_id: str) -> ObservationSnapshot | None` — most recent by `snapshot_timestamp DESC`.
   - `list_snapshots(session, entity_id, from_time, to_time) -> list[ObservationSnapshot]` — ordered by `snapshot_timestamp`.
   - Deps: step 2.
   - Verification: integration test against real TimescaleDB.
   - Complexity: moderate.

6. **Alembic migration** — `observation_snapshots` hypertable.
   - Partitioning: `snapshot_timestamp`, 1-day chunks (DOC-014 § TimescaleDB Hypertables).
   - Compression: compress chunks older than 7 days.
   - Index: `(entity_id, snapshot_timestamp DESC)` — DOC-014 § Indexing Strategy: "Same PIT pattern, one schema over."
   - Deps: step 5.
   - Verification: `make migrate` on fresh container; `\d observation_snapshots` shows correct schema.
   - Complexity: moderate.

### Phase D: Projection Engine (core artifact)

7. **`src/onchain_platform/analytics/projection_engine.py`** — State Projection engine.
   - Consumes FINALIZED facts that affect pool reserves: SWAP_EXECUTED, LIQUIDITY_ADDED, LIQUIDITY_REMOVED.
   - For each such fact: extract pool_address from payload, compute new reserves, update StateProjection in Redis.
   - Reserve computation:
     - SWAP_EXECUTED: reserves change based on the swap amounts (amount0_in/out, amount1_in/out). The new reserve = old reserve + amount_in - amount_out for each token.
     - LIQUIDITY_ADDED: reserves increase by amount0, amount1.
     - LIQUIDITY_REMOVED: reserves decrease by amount0, amount1.
   - Price computation: `price = reserve1 / reserve0` (token1 per token0, Decimal, DOC-012 § B.2).
   - All intermediate math uses Decimal (DOC-008 § Financial Precision).
   - `update_projection(session, redis_client, fact: BlockchainFact) -> None` — main entry point, called by the handler in main.py after a fact is persisted.
   - `rebuild_from_facts(session, redis_client, chain_id: int) -> None` — replays all FINALIZED facts from genesis (or from last snapshot) to rebuild state. Called on startup.
   - Deps: steps 1, 4, 5.
   - Verification: unit tests with synthetic swap sequences, assert reserves and price.
   - Complexity: **high** (the highest correctness bar in M5 — Decimal math, reserve computation).

8. **`src/onchain_platform/platform/scheduler.py`** — Snapshot scheduler.
   - APScheduler-based periodic task (DOC-010 § Job Scheduling).
   - Every N seconds (configurable, default 60): reads current StateProjection from Redis for all active pools, writes ObservationSnapshot to TimescaleDB.
   - Skip if no new facts since last snapshot (check `as_of_fact_id` against last snapshot's `as_of_fact_id`).
   - Deps: steps 4, 5.
   - Verification: unit test — scheduler calls snapshot function at expected interval.
   - Complexity: moderate.

### Phase E: Integration into Pipeline

9. **`src/onchain_platform/main.py`** — Wire projection engine + snapshot scheduler.
   - After saving a fact and running entity resolution, call `projection_engine.update_projection()` for SWAP_EXECUTED, LIQUIDITY_ADDED, LIQUIDITY_REMOVED facts.
   - On startup: call `projection_engine.rebuild_from_facts()` to rebuild state from facts (or from last snapshot).
   - Start snapshot scheduler.
   - Deps: steps 7, 8.
   - Verification: `make lint`, `make typecheck`, `make import-check` pass.
   - Complexity: moderate.

### Phase F: Tests

10. **`tests/unit/test_projection_engine.py`** — Unit tests for projection engine.
    - `test_swap_updates_reserves_and_price`: apply a known swap, assert reserves and price match expected Decimal values.
    - `test_liquidity_add_increases_reserves`: apply a LiquidityAdded fact, assert reserves increase.
    - `test_liquidity_remove_decreases_reserves`: apply a LiquidityRemoved fact, assert reserves decrease.
    - `test_price_always_token1_per_token0`: verify price direction consistency.
    - `test_all_intermediate_math_is_decimal`: verify no float in computation path.
    - Deps: step 7.
    - Verification: `make test` green.
    - Complexity: high.

11. **`tests/integration/test_state_projection.py`** — Integration tests against real Redis + Postgres.
    - `test_state_projection_correct_after_swap`: process a swap fact, verify StateProjection in Redis matches expected reserves/price.
    - `test_state_projection_rebuild_on_restart`: process facts, delete Redis key, rebuild from facts, verify identical state.
    - `test_state_projection_reorg_orphans_resets_state`: process facts, orphan some, verify state reflects only remaining FINALIZED facts.
    - Deps: steps 4, 7.
    - Verification: `make test` green with compose up.
    - Complexity: high.

12. **`tests/integration/test_observation_snapshots.py`** — Integration tests for snapshots.
    - `test_snapshot_captures_current_state`: process facts, take snapshot, verify snapshot fields match StateProjection.
    - `test_snapshot_upsert_on_duplicate_timestamp`: take two snapshots at same timestamp, verify latest wins.
    - `test_point_in_time_query`: take snapshots at T1 and T2, query at T1, verify T1's values returned.
    - Deps: steps 5, 7, 8.
    - Verification: `make test` green.
    - Complexity: moderate.

13. **`tests/replay/test_replay.py`** — Extend replay test with state verification.
    - After replaying the fixture, verify that StateProjection for the pool reflects the expected reserves.
    - Deps: step 7.
    - Verification: `make test-replay` green.
    - Complexity: moderate.

### Phase G: Final Gate

14. **Final gate + commit.**
    - `make lint && make typecheck && make import-check && make test && make test-replay` all green.
    - Update ImplementationPlan.md Milestone 5 DoD checkboxes.
    - Commit.

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| State Projection drift from actual chain state | Medium | High | `rebuild_from_facts()` on startup ensures state is always derived from facts (DOC-006: "State can always be reconstructed by replaying Facts"). Periodic reconciliation against RPC `getReserves()` is a future hardening step, not M5. |
| Redis cache loss (restart, crash) | High | Medium | DOC-012 § B.2: "Never persisted as its own historical table — it is served from Redis cache and can always be rebuilt by replaying Facts." On startup, `rebuild_from_facts()` restores state. Redis is transport, not truth (ADR-006). |
| Snapshot timestamp collision | Low | Low | Upsert on `(entity_id, snapshot_timestamp, source)` composite key (DOC-012 § B.3). Source distinguishes concurrent snapshotters. |
| State Projection computation error (wrong price formula) | Medium | High | Unit tests against known swap sequences with expected reserves/price. Price = reserve1/reserve0 (token1 per token0, DOC-012 § B.2). All Decimal math (DOC-008). |
| Snapshot scheduler performance | Low | Medium | Skip if no new facts since last snapshot (check `as_of_fact_id`). Configurable interval. No snapshot for entities with no StateProjection. |
| Reorg affects state but snapshot already captured wrong state | Medium | High | On reorg: delete StateProjection for affected pools from Redis, delete ObservationSnapshots in orphaned block range, recompute state from remaining FINALIZED facts. The projection engine's `rebuild_from_facts()` handles this. |
| `reserve0`/`reserve1` computation requires knowing which token is token0/token1 | Medium | Medium | The pool's TradingPair entity (from M4) stores `base_token_id` and `quote_token_id`. The projection engine queries the TradingPair to determine token ordering. If the TradingPair doesn't exist yet (entity resolution lag), skip projection for that fact — it will be caught on the next rebuild. |
| Redis connection failure during projection update | Low | Medium | Wrap Redis calls in try/except, translate to TransportError (DOC-013 § Exception Hierarchy). Log warning, continue processing — the fact is already persisted; state can be rebuilt on next attempt. |

---

## 4. Definition of Done Matrix

| DoD Item (ImplementationPlan § Milestone 5) | Verification Method | Automated? |
|---|---|---|
| Killing and restarting the Projection Engine rebuilds identical state purely by replaying Facts | Integration test: start engine, process facts, delete Redis key, restart engine, assert state byte-identical | Yes |
| State Projection correctly reflects current pool reserves | Unit test: apply known swap sequence, assert reserves match expected Decimal values | Yes |
| Observation Snapshot correctly captures state at a specific timestamp | Integration test: process facts, take snapshot at T, query snapshot at T, assert values match | Yes |
| `lint-imports` still passes | `make import-check` | Yes |
| Price is always token1 per token0 | Unit test: verify price direction consistency across swap directions | Yes |
| All intermediate math is Decimal | Unit test: verify no float in computation path | Yes |
| Snapshot upsert on duplicate timestamp | Integration test: two snapshots at same T, latest wins | Yes |
| Point-in-time query returns correct historical snapshot | Integration test: snapshots at T1/T2, query at T1 returns T1's values | Yes |

---

## 5. Out-of-Scope Confirmation

Per ImplementationPlan § Milestone 5 and § What Not To Build Yet:

- [x] Feature Engineering — NOT built. `analytics/feature_engine.py` not created (M6).
- [x] Intelligence / Risk Analysis — NOT built (M7).
- [x] Outcome Engine — NOT built (M8).
- [x] API endpoints — NOT built. `research/` stays empty (M9).
- [x] Dashboard updates — NOT built (M9).
- [x] Holder count tracking — NOT built. Requires token transfer events (Transfer facts), not in M1-M4 fact types. `holder_count` is None in ObservationSnapshot.
- [x] Market cap / FDV calculation — NOT built. Requires external price oracle. `market_cap_usd` and `fdv_usd` are None.
- [x] Multi-chain state projection — NOT built. Base only for M5.
- [x] `liquidity_usd` computation — NOT built. Requires external price oracle. `liquidity_usd` is None.
- [x] Redis Streams integration — NOT introduced. Direct function call pattern continues.
- [x] No `utils/` or `common/` package.
- [x] No second chain beyond Base.

---

## 6. Questions / Blockers

Q1 (needs human): The projection engine needs to know which token is token0/token1 for a pool to compute reserves correctly. The TradingPair entity (from M4) stores `base_token_id` and `quote_token_id`. But the SWAP_EXECUTED payload has `pool_address`, not `pair_id`. The projection engine needs to look up the TradingPair by `pool_address` to determine token ordering. Should the projection engine query the `trading_pairs` table directly, or should it receive the token ordering as a parameter? Recommendation: query directly — the projection engine has access to the session, and the TradingPair is already resolved by M4's entity resolution.

Q2 (design note): The `StateProjection.price` field is "token1 per token0" (DOC-012 § B.2). But which token is token0 vs token1 is determined by the Uniswap V2 factory's sorting algorithm (lexicographic address sort), not by any human convention. The projection engine must use the same ordering as the TradingPair's `base_token_id`/`quote_token_id` to ensure consistency. Flagging so the implementation doesn't accidentally invert the price.

Q3 (needs human): The snapshot scheduler needs to know which pools are "active" (have received facts recently). Should it scan all TradingPairs, or only pools that have a StateProjection in Redis? Recommendation: only pools with a StateProjection in Redis — this avoids snapshotting empty pools and reduces write amplification.

Q4 (design note): DOC-012 § B.2 says StateProjection is "Never persisted as its own historical table." But the projection engine needs to read the current state to compute the new state after a swap. The current state lives in Redis. The engine reads from Redis, computes the new state, and writes back to Redis. This is correct — the engine never reads from a historical StateProjection table (because none exists).

No hard blockers: every Q above has a stated fallback that keeps M5 buildable today.
