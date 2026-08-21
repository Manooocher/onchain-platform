# Milestone 6 Execution Plan — Feature Engineering

Status: Planning artifact. Implements `docs/implementation/ImplementationPlan.md` § Milestone 6.
Prepared: 2026-08-20, after re-reading ImplementationPlan § Milestone 6, DOC-012 § B.3 (Feature
schema + Naming Convention), DOC-013 § Determinism Discipline, DOC-014 § TimescaleDB Hypertables
/ § Indexing Strategy, DOC-008 § Point-in-Time Correctness.

Goal (verbatim from ImplementationPlan): the first two or three real Features, Polars-backed,
Point-in-Time correct.

Definition of Done (verbatim): a backtest-style query (as_of set to a past timestamp) and a live
query (as_of defaulted to now) for the same feature use the same code path — this is the actual,
executable meaning of "Point-in-Time correctness," not a principle to take on faith.

---

## 0. Pre-Flight Status

Verified against HEAD 7861ca8 (commands run, not assumed):

| Item | Status | Detail |
|---|---|---|
| M5 gates pass | ✅ Done | ruff clean, mypy strict 89 files 0 issues, lint-imports 8/8, 103 fast + 6 replay + 1 live smoke |
| `analytics/feature_engine.py` does NOT exist | ✅ Confirmed | `analytics/` contains `projection_engine.py`, `trade_aggregator.py`, `__init__.py`. |
| `domain/schemas/feature.py` does NOT exist | ✅ Confirmed | Not in `domain/schemas/`. Must be created (DOC-012 § B.3). |
| `persistence/timescale/repositories.py` has no FeatureRow | ✅ Confirmed | Only `MarketBarRow` and `ObservationSnapshotRow`. |
| Polars in pyproject.toml | ✅ Confirmed | `polars>=1.42.1` in dependencies. |
| Polars NOT used in production code | ✅ Confirmed | `grep -r "import polars" src/` returns empty. First usage in M6. |
| ObservationSnapshot data available | ✅ Confirmed | `observation_snapshot.py` schema + hypertable + CRUD from M5. |
| MarketBar data available | ✅ Confirmed | `market_bar.py` schema + hypertable + CRUD from M3. |

**Pre-flight summary:** all M5 artifacts in place, all M6 prerequisites absent. The Feature
schema, feature engine, features hypertable, and PIT query all need to be created from scratch.
Polars is installed but unused — M6 is its first production usage.

---

## 1. Open Decisions — Resolved

| Decision | Resolution | Rationale |
|---|---|---|
| Feature computation trigger | **Scheduled batch via APScheduler.** Features are analytical, not real-time. A periodic job (configurable interval, default 1 hour) computes Features for all active pools. Not on every snapshot (too frequent), not on-demand (too slow for dashboards). | DOC-012 § B.3: Features are "derived from Facts, Observation Snapshots, Market Bars, Metadata." They're batch computations, not event-driven. APScheduler is already in DOC-010 § Job Scheduling. |
| Initial feature set | **Two features:** `liquidity_growth_pct_1h` (suffix `_pct`) and `price_momentum_zscore_1h` (suffix `_zscore`). Both are simple, well-understood, and prove the Polars + PIT shape. | ImplementationPlan § Milestone 6: "Start with something simple and well-understood — `liquidity_growth_pct_1h` and `price_momentum_zscore_1h` are enough to prove the shape." Both have proper suffixes per DOC-012 § Feature Naming Convention. |
| Polars usage pattern | **Polars DataFrame for vectorized computation, SQL for data retrieval.** Load ObservationSnapshots/MarketBars from TimescaleDB into Polars DataFrames, compute Features vectorized, write results back. | DOC-010 § Data Processing: "Polars — Columnar, SIMD-optimized, parallel execution; Apache Arrow memory format; streaming capability. The standard for modern quant platforms." SQL for retrieval (TimescaleDB indexes), Polars for computation (vectorized math). |
| PIT query implementation | **`get_feature_at(session, entity_id, feature_name, as_of)` → `SELECT * FROM features WHERE entity_id = :eid AND feature_name = :fn AND as_of_timestamp <= :as_of ORDER BY as_of_timestamp DESC LIMIT 1`.** Uses the index `(entity_id, feature_name, as_of_timestamp DESC)` from DOC-014 § Indexing Strategy. | DOC-012 § B.3: "`as_of_timestamp` — The point-in-time this value is valid for. This is the field every Point-in-Time-correctness query filters on." DOC-014 § Indexing Strategy: index already specified. |
| `inputs` field population | **List of ObservationSnapshot.snapshot_id and/or MarketBar.bar_id values used in the computation window.** For `liquidity_growth_pct_1h`: two snapshot IDs (current and 1h ago). For `price_momentum_zscore_1h`: list of MarketBar bar_ids in the 1h window. | DOC-012 § Traceability Chain: "every Feature must have a non-empty `inputs` list. An empty `inputs` list is a bug, not an edge case." Storing IDs (not full objects) keeps the field compact. |
| Replay test semantics for float | **Tolerance `1e-10` for `Feature.value`.** All other fields remain byte-identical (str/int/enum). | DOC-013 § Determinism Discipline: "Replay Tests verifying the two genuine float fields must use a mathematical tolerance (e.g., `assert abs(a - b) < 1e-10`)." DOC-010 § Testing: same rule. |

---

## 2. Build Order (Sequential)

Gates: every step passes `make lint && make typecheck && make import-check` before the next begins.

### Phase A: Domain Layer (Feature schema)

1. **`src/onchain_platform/domain/schemas/feature.py`** — Feature schema (DOC-012 § B.3).
   - Frozen Pydantic model. Fields: `schema_version`, `feature_id` (`f"{feature_name}|{entity_id}|{as_of_timestamp.isoformat()}"` — `|` delimiter per DOC-012 § Composite ID Delimiter), `feature_name`, `entity_id`, `entity_type` (enum: TRADING_PAIR | WALLET | TOKEN), `as_of_timestamp`, `computed_at`, `window` (str | None), `value` (float — the first genuine float field), `inputs` (list[str]).
   - Validator: `feature_name` must end with `_pct`, `_ratio`, `_score`, `_zscore`, `_usd`, or `_delta` (DOC-012 § Feature Naming Convention).
   - Validator: `inputs` must be non-empty (DOC-012 § Traceability Chain: "An empty inputs list is a bug, not an edge case").
   - Deps: none.
   - Verification: unit test round-trip, naming-convention validator, frozen-mutation rejection.
   - Complexity: moderate.

2. **`src/onchain_platform/domain/schemas/enums.py`** — add `EntityType` enum.
   - `EntityType(StrEnum)`: TRADING_PAIR, WALLET, TOKEN (DOC-012 § B.3 Feature schema).
   - Deps: none.
   - Verification: unit test asserting member values.
   - Complexity: trivial.

3. **`tests/unit/test_feature_schema.py`** — unit tests for Feature schema.
   - Round-trip, naming-convention validator (reject names without suffix), frozen-mutation rejection, empty-inputs rejection, feature_id format validation.
   - Deps: steps 1, 2.
   - Verification: `make test` green.
   - Complexity: trivial.

### Phase B: Persistence Layer (features hypertable)

4. **`src/onchain_platform/persistence/timescale/repositories.py`** — add `FeatureRow` ORM model + CRUD.
   - Column types per DOC-014: `feature_id` TEXT, `feature_name` TEXT, `entity_id` TEXT, `entity_type` native ENUM, `as_of_timestamp` TIMESTAMPTZ, `computed_at` TIMESTAMPTZ, `window` TEXT (nullable), `value` DOUBLE PRECISION (DOC-014 § Type Mapping Rules: "Genuinely float" category — Feature.value is one of only two sanctioned float fields), `inputs` TEXT[].
   - Composite PK: `(feature_id, as_of_timestamp)` — TimescaleDB hypertable requires partitioning column in PK (same pattern as market_bars, observation_snapshots).
   - Upsert on `(feature_name, entity_id, as_of_timestamp)` — idempotent re-computation.
   - `save_feature(session, feature: Feature) -> bool`
   - `get_feature_at(session, entity_id, feature_name, as_of) -> Feature | None` — PIT query: most recent with `as_of_timestamp <= as_of`, ordered DESC, LIMIT 1.
   - `list_features(session, entity_id, feature_name, from_time, to_time) -> list[Feature]`
   - Deps: step 1.
   - Verification: integration test against real TimescaleDB.
   - Complexity: moderate.

5. **Alembic migration** — `features` hypertable.
   - Partitioning: `as_of_timestamp`, 1-day chunks (DOC-014 § TimescaleDB Hypertables).
   - Compression: compress chunks older than 7 days.
   - Index: `(entity_id, feature_name, as_of_timestamp DESC)` — DOC-014 § Indexing Strategy: "The Point-in-Time pattern."
   - Native ENUM for `entity_type`.
   - Deps: step 4.
   - Verification: `make migrate` on fresh container; `\d features` shows correct schema.
   - Complexity: moderate.

### Phase C: Feature Engine (core artifact)

6. **`src/onchain_platform/analytics/feature_engine.py`** — Feature computation engine.
   - `compute_liquidity_growth_pct_1h(session, entity_id, as_of) -> Feature | None`:
     - Reads ObservationSnapshots for entity_id in [as_of - 1h, as_of].
     - If fewer than 2 snapshots: return None (insufficient data).
     - Computes: `(latest_reserve0 - oldest_reserve0) / oldest_reserve0 * 100` (Decimal intermediate, final output as float).
     - `inputs` = list of snapshot_ids used.
     - `value` = float result (the first genuine float field).
   - `compute_price_momentum_zscore_1h(session, entity_id, as_of) -> Feature | None`:
     - Reads MarketBars for entity_id in [as_of - 1h, as_of].
     - If fewer than 2 bars: return None.
     - Computes: z-score of price changes (close - open) over the window.
     - Uses Polars DataFrame for vectorized computation.
     - `inputs` = list of bar_ids used.
     - `value` = float z-score.
   - All computation uses Polars for vectorization (DOC-010).
   - All input data is PIT-filtered (`snapshot_timestamp <= as_of`, `bar_start_time <= as_of`).
   - No wall-clock reads (DOC-013 § Determinism Discipline) — `as_of` is always a parameter.
   - No `set` iteration on aggregation paths (DOC-013).
   - Deps: steps 1, 4.
   - Verification: unit tests with synthetic data, assert exact float values within tolerance.
   - Complexity: **high** (the highest correctness bar in M6 — PIT correctness, Polars integration, float semantics).

7. **`tests/unit/test_feature_engine.py`** — unit tests for feature engine.
   - `test_liquidity_growth_basic`: 2 snapshots, reserve0 goes from 1000 to 1500 → value = 50.0.
   - `test_liquidity_growth_insufficient_data`: 1 snapshot → returns None.
   - `test_price_momentum_zscore_basic`: 3 bars with known prices → assert z-score within tolerance.
   - `test_pit_filtering_only_uses_data_up_to_as_of`: snapshots at T1, T2, T3; compute at T2 → only T1, T2 used.
   - `test_inputs_populated_with_snapshot_ids`: verify `inputs` list is non-empty and contains correct IDs.
   - Deps: step 6.
   - Verification: `make test` green.
   - Complexity: high.

### Phase D: Integration + PIT Query

8. **`tests/integration/test_feature_engine.py`** — integration tests against real Postgres/TimescaleDB.
   - `test_feature_computation_from_real_snapshots`: insert ObservationSnapshots, compute feature, verify value and inputs.
   - `test_feature_computation_from_real_bars`: insert MarketBars, compute z-score feature, verify value.
   - `test_pit_query_returns_correct_feature`: insert features at T1, T2, T3; query at T2 → returns T2's feature.
   - `test_pit_query_returns_latest_for_live`: insert features at T1, T2; query with `as_of=None` (defaults to now) → returns T2's feature.
   - `test_feature_upsert_idempotent`: compute same feature twice → no duplicate, same value.
   - Deps: steps 4, 6.
   - Verification: `make test` green with compose up.
   - Complexity: high.

9. **`tests/replay/test_replay.py`** — extend replay test with Feature verification.
   - After replaying the fixture, compute Features and verify they are deterministic within tolerance (`abs(a - b) < 1e-10` for float fields, byte-identical for all others).
   - Deps: step 6.
   - Verification: `make test-replay` green.
   - Complexity: moderate.

### Phase E: Scheduler Integration (optional, can defer)

10. **`src/onchain_platform/platform/scheduler.py`** — register Feature computation as APScheduler job.
    - Periodic job (configurable interval, default 1 hour) that computes Features for all active pools.
    - Reads active pools from Redis state keys (same pattern as snapshot scheduler in M5).
    - Deps: step 6.
    - Verification: unit test — scheduler calls feature computation at expected interval.
    - Complexity: moderate.

11. **`src/onchain_platform/main.py`** — wire feature computation scheduler.
    - Start APScheduler with feature computation job on startup.
    - Deps: step 10.
    - Verification: `make lint`, `make typecheck`, `make import-check` pass.
    - Complexity: trivial.

### Phase F: Final Gate

12. **Final gate + commit.**
    - `make lint && make typecheck && make import-check && make test && make test-replay` all green.
    - Update ImplementationPlan.md Milestone 6 DoD checkboxes.
    - Commit.

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Polars multi-threaded aggregation produces non-deterministic float round-off | High | High | DOC-013 § Determinism Discipline: "Replay Tests verifying the two genuine float fields must use a mathematical tolerance (e.g., `assert abs(a - b) < 1e-10`)." Never assert byte-identical for float fields. All other fields (str/int/enum) remain byte-identical. |
| `set` iteration on aggregation path breaks reproducibility | Medium | High | DOC-013: "set is: its iteration order depends on hash randomization (PYTHONHASHSEED) and is not guaranteed stable across processes." Collect into `list` (or `dict` with deterministic ordering) before aggregating. Enforced by code review + ruff ASYNC rules. |
| Feature naming convention violation (missing suffix) | Low | Medium | Validator on `feature_name` field rejecting names without `_pct`/`_ratio`/`_score`/`_zscore`/`_usd`/`_delta` (step 1). Unit test asserts rejection. |
| PIT query returns wrong Feature due to missing index | Medium | High | Alembic migration creates the index `(entity_id, feature_name, as_of_timestamp DESC)` (step 5). Integration test verifies PIT query uses the index (EXPLAIN check optional). |
| `inputs` field bloat for large computation windows | Low | Low | For M6's 1h windows, inputs are 2–12 IDs. Not a concern at MVP scale. Cap at 100 inputs if needed in future. |
| Feature computation on empty data (new pair with no history) | Medium | Medium | Return `None` if insufficient data (fewer than 2 snapshots/bars). Do not create Feature with `value=0.0` — that would be a misleading data point. Unit test covers this case. |
| `Decimal` → `float` conversion loses precision in intermediate computation | Medium | Medium | Python's `Decimal` → `float` conversion is exact for values within float64 range. For `liquidity_growth_pct_1h`: `(Decimal("1500") - Decimal("1000")) / Decimal("1000") * 100` → `Decimal("50.0")` → `float(Decimal("50.0"))` = `50.0` (exact). For z-score: Polars handles float64 natively. Tolerance in replay tests covers any residual. |
| APScheduler job runs while feature computation is still running from previous interval | Low | Medium | APScheduler's default behavior is to skip if previous job is still running. Configure `max_instances=1` per job. |

---

## 4. Definition of Done Matrix

| DoD Item (ImplementationPlan § Milestone 6) | Verification Method | Automated? |
|---|---|---|
| Backtest-style query (`as_of` past) and live query (`as_of` now) use same code path | Integration test: `get_feature_at(session, eid, fn, past_timestamp)` and `get_feature_at(session, eid, fn, None)` both call the same function, both return Feature objects | Yes |
| Features are Polars-backed | Code review: `feature_engine.py` imports and uses `polars`. Unit test: computation uses Polars DataFrame. | Yes |
| Features are Point-in-Time correct | Integration test: Feature computed at T only uses data with `timestamp <= T`. Snapshot at T+1 does not affect Feature at T. | Yes |
| Feature names have required suffix | Unit test: validator rejects `liquidity_growth` (no suffix), accepts `liquidity_growth_pct_1h` | Yes |
| Replay test uses tolerance for float fields | Replay test: `assert abs(feature1.value - feature2.value) < 1e-10` | Yes |
| `lint-imports` still passes | `make import-check` | Yes |
| `inputs` list is non-empty | Unit test: validator rejects empty `inputs`, integration test verifies populated `inputs` | Yes |

---

## 5. Out-of-Scope Confirmation

Per ImplementationPlan § Milestone 6 and § What Not To Build Yet:

- [x] Intelligence / Risk Analysis — NOT built (M7).
- [x] Outcome Engine — NOT built (M8).
- [x] API endpoints — NOT built. Feature storage is internal only (M9).
- [x] Dashboard updates — NOT built (M9).
- [x] Feature Store abstraction — NOT built. Future phase.
- [x] ML model training on Features — NOT built. Phase 4 (DOC-005).
- [x] More than 2-3 initial Features — NOT built. Start simple, expand later.
- [x] Complex Features requiring external data — NOT built. `holder_count`, `wallet_concentration` require data not yet collected.
- [x] No Redis Streams integration — direct function call pattern continues.
- [x] No second chain beyond Base.
- [x] No `utils/` or `common/` package.

---

## 6. Questions / Blockers

Q1 (needs human): The `Feature.entity_type` field requires an `EntityType` enum (TRADING_PAIR | WALLET | TOKEN). This enum doesn't exist yet in `domain/schemas/enums.py` or `domain/enums.py`. Recommendation: add it to `domain/schemas/enums.py` alongside `BarInterval` (it's a schema-level enum, not a structural registry enum). Confirm this placement.

Q2 (design note): The `Feature.value` field is `float` — the first genuine float in the platform. All other financial fields are `Decimal`/`str`. This is intentional per DOC-012 § Conventions clarification: "any field that is a direct on-chain amount, price, or unmodified pass-through of one uses Decimal. Any field genuinely computed by the Feature Engine from one or more Decimal inputs is float in the Feature schema." The computation itself must still use Decimal inputs internally; only the final output value's storage type relaxes. Flagging so the implementation doesn't accidentally use float for intermediate Decimal math.

Q3 (design note): The `price_momentum_zscore_1h` feature computes a z-score of price changes. The formula is: `z = (x - mean) / std` where `x` is the latest price change, `mean` and `std` are computed from the 1h window. If `std = 0` (all price changes identical), the z-score is undefined. Recommendation: return `0.0` in this case (no momentum), and log a warning. Unit test covers this edge case.

Q4 (needs human): Should the feature computation scheduler be wired into `main.py` in M6, or deferred to M9 (when the dashboard needs it)? Recommendation: wire it in M6 — it's a small addition (APScheduler job registration) and proves the end-to-end shape. But it can be deferred if M6 scope is already large.

No hard blockers: every Q above has a stated fallback that keeps M6 buildable today.
