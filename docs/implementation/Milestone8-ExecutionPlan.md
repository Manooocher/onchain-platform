# Milestone 8 Execution Plan — Outcome Engine

> **Milestone 8 Goal:** ground-truth labels (`RUG_PULL`, `SUCCESSFUL_LAUNCH`, `DEAD_TOKEN`) exist for pairs whose observation window has closed.
>
> **Definition of Done:** the first cohort of pairs old enough to evaluate (per DOC-012's `observation_window`) gets a real, versioned `label_definition` applied — the ground truth Phase 4 (ML Foundation, DOC-005) will train against.
>
> This is **planning only**. No implementation code is written in this document; it specifies what to build, in what order, with which exact files and which resolved decisions. Verify every artifact exists before starting each phase.

---

## 0. Pre-Flight Status

Verified against the committed tree at `HEAD 5783709` (branch `master`, origin ahead 9, working tree clean except an unrelated untracked `AUDIT_REPORT_M8_PLANNING.md` artifact).

| # | Check | Status | Evidence |
|---|-------|--------|----------|
| 1 | M7 gates still pass | ✅ | `make lint` PASS (ruff + 110 formatted), `make typecheck` PASS (68 files, 0 issues), `make import-check` PASS (8/8 KEPT), `make test` **157 passed**, `make test-replay` **6 passed** |
| 2 | `analytics/outcome_engine.py` does NOT exist | ✅ confirmed | `ls analytics/` → feature_engine, projection_engine, trade_aggregator only; no outcome_engine |
| 3 | `domain/schemas/outcome.py` does NOT exist | ✅ confirmed | not in `schemas/`; grep for `outcome` in `src/` matches only `intelligence_job.py` (import) and `persistence/postgres/outcomes_insights.py` (file header) |
| 4 | `outcomes` table does NOT exist | ✅ confirmed | 14 tables end at `insights`; no `outcomes` |
| 5 | `observation_window` not configured anywhere | ✅ confirmed | `grep observation_window` in `config/`, `.env.example`, `src/` → nothing |
| 6 | `label_definition` / `label_definition_version` fields don't exist | ✅ confirmed | no references anywhere in `src/` or config |
| 7 | M7 risk signals available | ✅ | `risk_signals.py` (risk_score, is_honeypot, risk_indicators); risk rules + insights pipeline in M7. **Persistence caveat:** only `insights` are persisted to a table; raw `RiskSignals` are transient (see §1 decision D7) |
| 8 | M6 Features available | ✅ | `feature_engine.py` + PIT query `get_feature_at`; features `liquidity_growth_pct_1h`, `price_momentum_zscore_1h`. Note: not live-produced in DB (only test fixtures) |
| 9 | M5 Observation Snapshots available | ✅ | `observation_snapshots.ts_repos` `list_snapshots`; **`liquidity_usd` is NULL in all 6 rows** (M7 gap; 0/6 have a value) |
| 10 | APScheduler wired in `main.py` | ✅ | `create_feature_scheduler` (hourly feature job) + `add_job` for `intelligence_risk_scan` (5-min), both via callback pattern (lines 185, 210, 217, 226) |

**Data availability:** only 1 seeded `TradingPair`; **0 pairs are even 1h old**; snapshots/bars/features are test fixtures. → M8 must be tested with **synthetic replay fixtures** and default `observation_window = "1h"`.

---

## 1. Open Decisions — Resolved

| # | Decision | Resolution | Rationale |
|---|----------|-----------|-----------|
| D1 | **Observation window** | Start `"1h"`; parameterized; upgrade to `"24h"` once a live cohort exists. Support `{"1h","24h","7d"}` via a single time-delta mapping. | No pair ≥1h old in live DB; a 1h window is the smallest that produces evaluable synthetic cohorts and exercises the full path. `observation_window` is stored on each `Outcome` as a `str` (DOC-012 B.4) so historical rows always record which window was used. Configurable constant, not a magic number embedded in a rule. |
| D2 | **`RUG_PULL` rules** (V1, logic = ANY) | (a) **Honeypot:** a persisted `HoneypotDetected` insight exists for the entity; OR (b) **Liquidity collapse:** early-to-late reserve-product drop > **90%** within the window, where early/late probes are the first/last `ObservationSnapshot` in the window; guard early==0 (can't compute → False). | `liquidity_usd` is NULL (M7 gap), so we use the `reserve0 × reserve1` product as a **liquidity-depth proxy** (documented limitation, matches M7's accepted `reserve>0` proxy). Honeypot is the strongest rug signal and auto-validates M7's `is_honeypot` path. Reserve math stays `Decimal` (DOC-008). |
| D3 | **`SUCCESSFUL_LAUNCH` rules** (V1, logic = ALL) | (a) **not honeypot**; AND (b) **active market:** total `SWAP` trade_count over the window ≥ 30 (from `market_bars`); AND (c) **liquidity survived:** reserve-product at window end is ≥ 70% of its peak within the window. | A USD threshold (`> $10k`) is impossible without `liquidity_usd`; the reserve-product-survival test is the honest proxy and is deterministic. Trade-count floor proves real discovery/trading happened. All three must hold to avoid labeling a mere pump as a successful launch. |
| D4 | **`DEAD_TOKEN` rules** (V1, logic = ANY) | (a) **zero swaps** across the entire window (total trade_count == 0 from `market_bars`); OR (b) **liquidity fully removed:** reserve-product at window end == 0 (reserves drained). | Death is best signaled by complete inactivity or complete liquidity withdrawal — both cheap and deterministic to query. `SUCCESSFUL_LAUNCH` and `DEAD_TOKEN` are **not mutually exclusive** labels; a pair can be both if a launch subsequently died. This is intended (labels are per-`outcome_type` booleans). |
| D5 | **Label definition versioning** | Hardcode `OUTCOME_RULES_VERSION = "1.0"` as a module constant co-located with the rule tables in `analytics/outcome_rules.py`. Every emitted `Outcome.label_definition_version` copies this constant. Bump on any rule change. | DOC-012 B.4 requires `label_definition_version` versioned independently of `schema_version`; historical outcomes keep their original version forever (never rewritten). The constant is the single source of truth, mirroring M7's `RISK_RULES_VERSION` pattern. |
| D6 | **Scheduling** | **APScheduler `interval` job, every 1 hour**, wired via the M6/M7 callback pattern. New `analytics/outcome_job.py` exports a plain runner; `main.py` wraps it in a callback and registers the job. Add `id="outcome_evaluation"`, `max_instances=1`. Job intentionally runs far less often than intelligence (outcomes need ≥1h of observation anyway). | Outcomes are not latency-sensitive (unlike the 5-min intelligence scan); hourly is the natural cadence for a ≥1h window. Callback pattern is the proven, import-linter-compliant wiring (DOC-011: `platform/scheduler.py` must never import `analytics/`). |
| D7 | **`liquidity_usd` blocker** | **Option A — defer USD rules.** Use `reserve0 × reserve1` as the liquidity-depth proxy and that is it; no `liquidity_usd` computation in M8. Rich USD-based rules and the price oracle are documented as M9. | `liquidity_usd` requires an oracle that does not exist; building one expands M8's scope and delays the labels. The reserve proxy is deterministic and already used by M7's filter. Limitation recorded in code comments and this plan so it is not silently mistaken for a resolved signal. |
| D8 | **Evaluation trigger** | **Age-based, one-shot.** A pair is evaluable when `now − pair_creation_time ≥ observation_window`. Evaluate exactly once; the job skips any pair that already has an outcome for that `(entity_id, outcome_type)`. `evaluation_timestamp` is **deterministic** (creation_time + window), never `now`, so re-runs and replays produce the same `outcome_id`; `ON CONFLICT (outcome_id) DO NOTHING` guarantees idempotency. | DOC-012 defines `evaluation_timestamp` as "when the observation window closed" — deterministic by construction. One-shot avoids "outcome drift" on re-runs. PIT correctness then means: query only inputs with `timestamp ≤ evaluation_timestamp`. |
| D9 | **Honeypot signal source** | Read the persisted **`insights`** table (`insight_type = 'HoneypotDetected'`) rather than the transient `RiskSignals`. | M7 persists only `insights`; `RiskSignals` are not stored. If a pair was never scanned, no honeypot insight exists and the honeypot rule simply does not fire (the reserve-collapse rule still covers many rugs). This keeps M8 out of the "persist RiskSignals" scope creep. See §6 Q1. |

**Evaluation-timestamp/pair-age data source:** `pair_creation_time` is resolved as `event_time` of the `blockchain_facts` row whose `fact_id == trading_pairs.creation_fact_id` (the `PAIR_CREATED` fact). This is deterministic and requires no schema change to `trading_pairs` (which has no creation-time column). `trading_pairs.creation_block` is available but is a block number, not a wall-clock time — the fact join is the correct source.

---

## 2. Build Order (Sequential)

Strict dependency order; each step leaves the tree green and commits only when its phase gate passes. Reuse the M6/M7 one-phase-per-commit cadence. After each phase, run `make lint typecheck import-check`; run `make test` after phases that add tests.

### Phase A — Domain Layer (Schemas)
1. **`src/onchain_platform/domain/schemas/outcome.py`** (new) — frozen Pydantic `Outcome` per DOC-012 B.4:
   - Fields: `schema_version` (`"1.0"`), `outcome_id`, `entity_id`, `outcome_type`, `observation_window` (str), `label_definition` (human-readable rule description), `label_definition_version` (str), `evaluation_timestamp` (tz-aware), `evaluated_at` (tz-aware), `label_value` (bool).
   - Validator: `outcome_id` must split on `|` into exactly 3 parts (`entity_id|outcome_type|evaluation_timestamp.isoformat()`) — `|` delimiter per DOC-012 § Composite ID Delimiter. Timestamps validated tz-aware UTC. **No confidence field** (DOC-008: confidence belongs to Predictions).
   - A `create()` classmethod that derives `outcome_id` from components (matches the `MarketBar`/`ObservationSnapshot`/`ChainReorgEvent` factory convention).
2. **`src/onchain_platform/domain/schemas/enums.py`** (edit) — add `OutcomeType` `StrEnum` with `RUG_PULL`, `SUCCESSFUL_LAUNCH`, `DEAD_TOKEN`, placed alongside the other fact-lifecycle enums.
3. **`tests/unit/test_outcome_schema.py`** (new) — round-trip validation, `outcome_id` format validation (reject `:`-delimited and wrong arity), timezone-awareness validation, frozen-mutation rejection (`Outcome.model_copy(update=...)` allowed, direct attribute assignment raises).
   - **Gate A:** lint/typecheck/import-check pass; new unit tests pass. Commit.

### Phase B — Persistence Layer (`outcomes` table)
4. **`src/onchain_platform/persistence/postgres/outcomes_insights.py`** (edit) — add `OutcomeRow` mapping to a regular (non-hypertable) PostgreSQL table `outcomes`:
   - PK `outcome_id` (Text); `entity_id` (Text); `outcome_type` (Enum `OutcomeType`, native); `observation_window` (Text); `label_definition` (Text); `label_definition_version` (Text); `evaluation_timestamp` / `evaluated_at` (DateTime tz=true); `label_value` (Boolean NOT NULL).
   - CRUD: `save_outcome(session, outcome) -> bool` (async upsert `INSERT … ON CONFLICT (outcome_id) DO NOTHING` — returns False when a duplicate is skipped, i.e. idempotent), `list_outcomes_for_entity(session, entity_id, outcome_type=None)`, `get_latest_outcome(session, entity_id, outcome_type)`. Wrap all `SQLAlchemyError` → `PersistenceError` (DOC-013 Exception Hierarchy). `OutcomeRow` uses `OutcomeBase` (declare a second `DeclarativeBase` sibling to `InsightBase`, matching the file's existing split).
5. **`migrations/versions/<hex>_outcomes_table.py`** (new) — Alembic migration creating `outcomes`:
   - Index `(entity_id, outcome_type, evaluation_timestamp DESC)` per DOC-014 § Indexing Strategy (research "what happened to this entity").
   - `CHECK (label_value IS NOT NULL)` per DOC-014.
   - **Forward-only per DOC-014:** `downgrade()` DROPs the empty table is acceptable (it will be empty in dev), but no downgrade may touch populated outcome rows in a migrated env.
   - Chain from current head `b2c3d4e5f6a7` (insights).
6. **`tests/integration/test_outcome_persistence.py`** (new) — against real Postgres: insert upsert, duplicate insert returns False + rowcount unchanged, list-with-type-filter, get_latest ordering. Extend `tests/conftest.py` with a `clean_outcomes` fixture (TRUNCATE `outcomes`) and add `outcomes` to the isolation set.
   - **Gate B:** `make migrate` applies cleanly; integration tests pass; alembic head now points at the new migration. Commit.

### Phase C — Outcome Rules + Engine (Core)
7. **`src/onchain_platform/analytics/outcome_rules.py`** (new) — pure, deterministic, versioned rule definitions (no I/O, no wall-clock, no set iteration — DOC-013):
   - Module constant `OUTCOME_RULES_VERSION = "1.0"`.
   - Typed pure functions taking domain objects (`list[ObservationSnapshot]`, `list[MarketBar]`, honeypot `bool`) and returning booleans: `_liquidity_collapse`, `_liquidity_survived`, `_reserve_product_drained`, `_zero_swaps`, `_active_market`, etc. All reserve math in `Decimal` (DOC-008).
   - Three exported evaluators — `evaluate_rug_pull(snapshots, is_honeypot) -> bool`, `evaluate_successful_launch(snapshots, bars, is_honeypot) -> bool`, `evaluate_dead_token(snapshots, bars) -> bool` — each emitting a human-readable `label_definition` string and applying the D2/D3/D4 logic. Hardcode the threshold constants beside the functions.
   - **Import-linter constraint:** `analytics/` must never import `intelligence/` (intelligence sits *above* analytics; that would be an upward import). The default import CHECKed by `make import-check` still passes — do not add an `intelligence` import here; honeypot arrives as a plain `bool` from the caller.
8. **`src/onchain_platform/analytics/outcome_engine.py`** (new) — the core artifact:
   - `async evaluate_outcome(session, *, entity_id, outcome_type, observation_window, evaluation_timestamp, pair_creation_time, clock) -> Outcome | None`.
   - Resolves PIT-correct input windows: `window_start = evaluation_timestamp − observation_window_delta`, and queries `ts_repos.list_snapshots(session, entity_id, window_start, evaluation_timestamp)` and `ts_repos.list_bars(session, entity_id, BarInterval, window_start, evaluation_timestamp)`. **All queries filter `timestamp ≤ evaluation_timestamp`** (PIT — never uses post-close data).
   - Resolves `is_honeypot` from `outcomes_insights` (persisted `HoneypotDetected` insight on the entity).
   - Dispatches on `outcome_type` to the matching `outcome_rules` evaluator; builds the `Outcome` with `evaluation_timestamp` (fixed), `evaluated_at = clock()`, `label_definition_version = OUTCOME_RULES_VERSION`.
   - Returns `None` when the observation window opens before the pair existed (no data) — caller treats as "not evaluable yet".
9. **`tests/unit/test_outcome_rules.py`** (new) — determinism (same inputs → same boolean, call twice), threshold boundary cases (exactly 0.9 drop, exactly 30 trades), division-by-zero/empty-input guards, honeypot short-circuits.
10. **`tests/unit/test_outcome_engine.py`** (new) — PIT filtering (an input snapshot *after* `evaluation_timestamp` is ignored), correct `outcome_id`/`label_definition_version`/`label_value` assembly, `None` when insufficient data.
    - **Gate C:** all new unit tests pass; `make import-check` still 8/8 (critical — proves the engine honored the analytics-below-intelligence boundary). Commit.

### Phase D — Scheduler Integration
11. **`src/onchain_platform/persistence/postgres/entity_repositories.py`** (edit) — add `list_all_trading_pairs(session) -> list[TradingPair]` (the suite currently lacks a "list all pairs"; the job must not query ORM directly above the persistence boundary).
12. **`src/onchain_platform/analytics/outcome_job.py`** (new) — `async run_outcome_evaluation(pg_engine, redis_client, chain_id, clock)`:
    - List all `TradingPair`s; for each, resolve `pair_creation_time` via `repositories.get_fact(creation_fact_id).event_time`.
    - For each active `observation_window` in the configured set, skip the pair if a `get_latest_outcome(entity_id, outcome_type)` already exists (one-shot).
    - When `clock() − pair_creation_time ≥ window`, evaluate all three `outcome_type`s and persist via `save_outcome` (idempotent `DO NOTHING`).
    - `pairs_evaluated`, `outcomes_created`, `pairs_skipped` counters logged with mandatory `chain_id` (DOC-013 Observability).
13. **`src/onchain_platform/main.py`** (edit, composition root — exempt from import-linter) — add an `_evaluate_outcomes()` async callback around `run_outcome_evaluation(engine, redis_client, settings.chain_id, _clock)` and register:
    `scheduler.add_job(_evaluate_outcomes, "interval", hours=1, id="outcome_evaluation", max_instances=1)`.
    **Do NOT touch `platform/scheduler.py`** (it must stay Capability-agnostic).
14. **`tests/integration/test_outcome_engine.py`** (new) — known-scenario labels: seed a pair + snapshots + bars producing RUG_PULL, one producing SUCCESSFUL_LAUNCH, one DEAD_TOKEN; assert correct per-type `label_value`, versioned `label_definition_version`, and PIT exclusion of post-close data.
15. **`tests/integration/test_outcome_job.py`** (new) — idempotency: run the job twice, assert outcome row-count unchanged (second run skips); age-based: a pair younger than the window is not evaluated.
    - **Gate D:** integration tests pass; import-linter 8/8; `make typecheck` clean (no `Any` in the new Capability interfaces). Commit.

### Phase E — Replay Test (determinism/reproducibility)
16. **`tests/replay/fixtures/`** — add a small fixed cohort: a `PAIR_CREATED` fact plus a handful of `SWAP_EXECUTED` facts and snapshots spanning exactly one observation window, with **fixed timestamps in the past** (so `clock() − creation ≥ window` holds with a pinned injected clock) and fixed reserve trajectories that deterministically trigger each label.
17. **`tests/replay/test_replay_outcomes.py`** (new) — drive the fixture through `run_outcome_evaluation` with a pinned clock and assert **byte-identical** `Outcome` output on two consecutive runs (allowed and expected: `label_value` is `bool`/enum/string, not `float`; DOC-013 forbids byte-identical replay only for `Feature.value`).
    - **Gate E:** `make test-replay` counts rise to ≥7 and all pass. Commit.

### Phase F — Final Gate
18. **Quality gates:** `make lint` (ruff), `make typecheck` (mypy strict, 0 issues), `make import-check` (**8/8 KEPT**), `make test` (unit+integration+schema), `make test`-extended to include new suites, `make test-replay` (≥7). 
19. **`docs/implementation/ImplementationPlan.md`** — tick M8 DoD checkboxes (cohort gets versioned `label_definition`; versioning enforced; deterministic).
20. **Final commit** on `master`, pushed to `origin/master`.
    - **Gate F:** all gates green on the final tree; then write the M8 milestone summary (execution-log convention from M1–M7).

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `liquidity_usd` unavailable (M7 gap) | High | Medium | Use `reserve0 × reserve1` liquidity-depth proxy (Decimal). Record limitation in code + plan. USD rules deferred to M9 (D7). |
| Outcome rules too simplistic → false pos/neg labels | High | Low | Rules are an explicit MVP baseline, versioned (`1.0`), documented. Labels are not silently corrected; a new rule version only affects pairs evaluated under that version (D5). |
| Observation window too short (1h) for meaningful labels | Medium | Medium | Start 1h for determinism; upgrade to 24h once a live cohort exists (D1). Window is stored per-Outcome so mixed windows never corrupt a cohort. |
| No real data to test with | High | Medium | Synthetic replay fixtures with pinned past timestamps (Phase E); live testing explicitly deferred and documented (Pre-Flight). |
| Outcome evaluation becomes non-deterministic | Medium | **High** | No wall-clock, no set iteration, no unseeded randomness (DOC-013); deterministic `evaluation_timestamp` = creation+window (D8); pure rule functions; replay test asserts byte-identical `Outcome`. |
| Label definition versioning not enforced | Medium | **High** | Hardcoded `OUTCOME_RULES_VERSION` constant (D5); `label_definition_version` copied at write; migration policy (Phase B) forbids rewriting historical rows. |
| Import-linter violation (analytics imports intelligence) | Low | **High** | Honeypot signal arrives as a caller-supplied `bool` from persisted `insights`; `outcome_rules`/`outcome_engine` never import `intelligence/`. `make import-check` runs every gate. |
| Job re-evaluates or duplicates outcomes across runs | Medium | Medium | One-shot guard (`get_latest_outcome` precheck) + `ON CONFLICT DO NOTHING` (D8). |
| A pair has no `PAIR_CREATED` fact resolvable (no creation time) | Low | Medium | `creation_fact_id` is NOT NULL on `trading_pairs`; `get_fact` returns it. On a missing fact row, skip and log (graceful, no partial outcome). |

---

## 4. Definition of Done Verification Matrix

| DoD Item (from `ImplementationPlan.md`) | Verification Method | Automated? |
|-----------------------------------------|---------------------|------------|
| First cohort of pairs gets a versioned `label_definition` | Integration test evaluates ≥3 synthetic pairs and asserts `label_definition` (non-empty, human-readable) and `label_definition_version == "1.0"` on every `Outcome` | Yes |
| Outcome evaluation is deterministic | Unit test: same rule inputs, called twice → same `label_value`; replay test: two consecutive runs produce byte-identical `Outcome` | Yes |
| Outcome evaluation is idempotent | Integration test: run job twice on same pair ⇒ no duplicate rows, `save_outcome` second call returns False | Yes |
| Outcomes are versioned & historical rows immutable | `OUTCOME_RULES_VERSION` constant present; integration asserts version written; migration policy forbids rewriting (documented + forward-only migration) | Partial (version write automated; immutability policy is convention + migration) |
| PIT correctness enforced | Integration test: an input snapshot/bar with timestamp *after* `evaluation_timestamp` is excluded from the rule inputs | Yes |
| `lint-imports` still passes | `make import-check` → 8/8 KEPT (run after every phase) | Yes |
| All quality gates green | `make lint` / `make typecheck` / `make test` (→ 157 + new suites) / `make test-replay` (→ ≥7) | Yes |
| Milestone write-up ticks in `ImplementationPlan.md` | Manually uncheck→checked M8 DoD; commit message references it | No |

---

## 5. Out-of-Scope Confirmation

This milestone explicitly does **NOT** include:
- [x] Machine Learning models (Phase 4, DOC-005)
- [x] `Prediction` schema (out of MVP, DOC-008: confidence belongs to Predictions, never Outcomes)
- [x] Real-time alerting on outcomes (later phase)
- [x] API endpoints for outcomes (M9, `research/api/`)
- [x] Dashboard visualization of outcomes (M9)
- [x] Multi-chain scanning beyond Base (MVP scope)
- [x] `liquidity_usd` computation / price oracle (deferred — M9; uses reserve proxy instead, D7)
- [x] Persisting raw `RiskSignals` (honeypot read from existing `insights`, D9)
- [x] Outcome re-evaluation after a window closes (one-shot evaluation, D8)
- [x] Complex/ML-based outcome rules (all rules deterministic, rule-based, versioned)

---

## 6. Questions / Blockers

**Q1 (needs human, non-blocking):** The `is_honeypot` signal is read from the persisted `insights` table (only M7 persisted artifact). If a pair was never GoPlus-scanned, no honeypot insight exists and that RUG_PULL sub-rule silently does not fire. Accept this for V1 (reserve-collapse rule still covers most rugs), or should M8 add persistence of `RiskSignals` (a new persisted artifact) so outcome rules read contract-level signals directly? **Recommendation (default path):** accept the `insights`-based read; defer `RiskSignals` persistence to a later milestone.

**Q2 (blocking, needs decision):** `observation_window` default `"1h"`. Confirm the platform should ship with a 30- to 60-minute `evaluation_timestamp` lag behind pair creation (i.e., pairs are labeled 1h after birth) for the MVP, given no live cohort yet. **Recommendation:** yes — 1h is the shortest window that yields deterministic synthetic fixtures; revisit to `"24h"` when real data accumulates (D1).

**Q3 (blocking, needs human check):** `pair_creation_time` is resolved by joining `trading_pairs.creation_fact_id → blockchain_facts.event_time`. This is deterministic but requires the `PAIR_CREATED` fact row to be `FINALIZED` (the M2 lifecycle) before outcome evaluation. Confirm that a pair whose creation fact is still `PENDING`/`CONFIRMED` should be skipped (recommended) rather than evaluated.

**Q4 (note):** The audit's earlier `AUDIT_REPORT_M8_PLANNING.md` sits untracked in the working tree. Harmless; leave it out of the M8 commit or delete before commit.

**Q5 (note):** The M2 `checkpoints.chain_id` auto-increment default and the order-dependent finality unit test (flaky under a full-suite-run, passes in isolation) are pre-existing debt, not M8 work; fix separately. M8 tests must not be written to depend on a `checkpoints` truncation that excludes this.

---

### Immediate next step after approval
Begin **Phase A step 1**: create `src/onchain_platform/domain/schemas/outcome.py` per DOC-012 B.4, then `OutcomeType` in `enums.py`, then `test_outcome_schema.py`. Do not proceed to Phase B until Gate A is green.