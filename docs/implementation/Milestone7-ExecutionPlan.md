# Milestone 7 Execution Plan — Intelligence (Basic Risk Analysis) with GoPlus Free Tier

Status: Planning artifact. Implements `docs/implementation/ImplementationPlan.md` § Milestone 7.
Prepared: 2026-08-20, after re-reading ImplementationPlan § Milestone 7, DOC-009 § Intelligence,
DOC-012 § B.4 (Insight schema), DOC-013 § Determinism Discipline / § Async Conventions,
ADR-006 Principle 5 (Buy Commodities), GoPlus Security API free-tier constraints.

Goal (verbatim from ImplementationPlan): a deterministic risk read on a pair — MVP's original
core hypothesis (DOC-003), now finally reachable.

Definition of Done (verbatim): a newly discovered pair gets a risk read within the same latency
budget as its first Feature computation — this is a research tool, not a batch report.

---

## 0. Pre-Flight Status

Verified against HEAD 5519003 (unit/schema tests pass; integration tests require compose stack
which is not running — infrastructure-only failures, not code issues):

| Item | Status | Detail |
|---|---|---|
| M6 unit/schema gates pass | ✅ Done | 79 unit+schema tests pass. Integration tests need compose (not running). |
| `intelligence/risk_rules.py` does NOT exist | ✅ Confirmed | `intelligence/` contains only `__init__.py`. |
| `intelligence/insight_generator.py` does NOT exist | ✅ Confirmed | Same. |
| `intelligence/goplus_client.py` does NOT exist | ✅ Confirmed | Same. |
| `intelligence/filter.py` does NOT exist | ✅ Confirmed | Same. |
| `persistence/postgres/outcomes_insights.py` does NOT exist | ✅ Confirmed | Not in `persistence/postgres/`. |
| `domain/schemas/insight.py` does NOT exist | ✅ Confirmed | Not in `domain/schemas/`. Must be created (DOC-012 § B.4). |
| `domain/schemas/risk_signals.py` does NOT exist | ✅ Confirmed | Must be created. |
| `.env.example` does NOT contain GOPLUS_API_KEY | ✅ Confirmed | Free tier works without key; optional key for higher limits. |
| APScheduler wired in main.py | ✅ Confirmed | 7 references — `create_feature_scheduler` wired with callback pattern. |
| `projection_engine.py` has liquidity_usd | ❌ Not available | `projection_engine.py` does NOT compute `liquidity_usd` (0 matches). M5 set it to None. Filter must use a different signal or compute it. |
| `feature_engine.py` has liquidity_growth/momentum | ✅ Confirmed | `compute_liquidity_growth_pct_1h` and `compute_price_momentum_zscore_1h` available. |

**Pre-flight summary:** all M6 artifacts in place, all M7 prerequisites absent. The `intelligence/`
package, Insight schema, RiskSignals schema, GoPlus client, filter layer, risk rules engine,
insight generator, and insights table all need to be created from scratch.

**Key gap:** `projection_engine.py` does NOT compute `liquidity_usd` — the filter cannot use it
directly. The filter must use an alternative signal (e.g., raw reserve amounts from StateProjection,
or MarketBar volume).

---

## 1. Open Decisions — Resolved

| Decision | Resolution | Rationale |
|---|---|---|
| **Filter layer design** | **Deterministic rules selecting ≤4,500 tokens/day:** (a) `liquidity_usd >= 5000` — but since `liquidity_usd` is unavailable, use `reserve0 > 0 AND reserve1 > 0` as a proxy (pool has liquidity). (b) Pool age ≤ 7 days (we want NEW pairs — `creation_block >= current_block - 7*24*3600/2`). (c) On supported DEX (Uniswap V2-style — `dex IN ('uniswap_v2')`). (d) Not already scanned by GoPlus in last 24h (dedup via Redis key `goplus_scanned:{chain_id}:{address}`). (e) Has at least 1 swap in last 24h (basic activity — query `blockchain_facts` for recent SWAP_EXECUTED). Sort by `reserve0 * reserve1` (proxy for liquidity depth) descending, take top N up to daily quota. | Deterministic, no wall-clock in rules (DOC-013), uses only data already available in the platform. The liquidity proxy (`reserve0 * reserve1`) is a reasonable heuristic until `liquidity_usd` is computed. |
| **Filter execution timing** | **Separate APScheduler job every 5 minutes.** Scans for NEW pairs meeting filter criteria that haven't been scanned yet. Uses callback pattern (proven in M6) to avoid import-linter violations. | Frequent enough to catch new pairs quickly, infrequent enough to stay within rate limits. 5-min interval = 288 scans/day, each selecting a batch of tokens. |
| **GoPlus API client design** | **HTTPX-based async client** with: (a) 30s timeout per call (DOC-013 § Async Conventions), (b) exponential backoff on 429/5xx (1s, 2s, 4s, max 3 retries), (c) token bucket rate limiter (140 CU/min capacity, refilled at ~2.33 tokens/sec), (d) 24h Redis caching (`goplus:{chain_id}:{address}`), (e) structured logging with chain_id, address, response_code. All httpx errors → `AcquisitionError` (DOC-013 § Exception Hierarchy). | HTTPX is already in deps (DOC-010). Token bucket is simple, deterministic, and respects the 150 CU/min limit with margin. Redis cache avoids redundant calls. |
| **Rate limiting strategy** | **Token bucket in Redis** with 140 tokens/min capacity, refilled at 2.33 tokens/sec. Track daily CU usage in Redis key `goplus_daily_cu:{date}`, stop processing at 28,000 CU/day (margin under 30,000 limit). Each API call costs 1 CU (single-address query). | Redis-based so it survives process restarts. 28,000/day leaves 2,000 CU margin for retries and manual scans. |
| **Risk signal schema** | **Frozen Pydantic model `RiskSignals`** with all extracted GoPlus fields as `str` (matching GoPlus's string-typed responses), plus computed `risk_score: float` (0.0–1.0) and `risk_indicators: list[str]` (human-readable). | Frozen per DOC-013. All GoPlus fields stored as strings (GoPlus returns strings). `risk_score` is float (same rationale as Feature.value — computed analytical output). |
| **Risk score computation** | **Deterministic weighted scoring:** honeypot detected → score=1.0 (auto-fail, immediate HIGH importance). Otherwise: hidden_owner (+0.3), owner_change_balance (+0.25), is_mintable (+0.15), sell_tax > 0.2 (+0.2), buy_tax > 0.2 (+0.15), is_proxy (+0.1), selfdestruct (+0.1), external_call (+0.05), transfer_pausable (+0.1), is_blacklisted (+0.5), is_airdrop_scam (+0.8), fake_token (+0.9). Score = min(sum, 1.0). All weights hardcoded, versioned for reproducibility. | Deterministic, no wall-clock, no set iteration (DOC-013). Weights are MVP baseline — iterate in future milestones. Versioned so historical scores remain explainable. |
| **Insight generation** | **Rule-based mapping from risk_score + indicators to Insights:** score >= 0.8 → "High Risk Detected" (HIGH), score >= 0.5 → "Moderate Risk Detected" (MEDIUM), honeypot → "Honeypot Detected" (HIGH), whale_concentration > 50% → "Whale Concentration" (MEDIUM), sell_tax > 0.2 → "High Sell Tax" (MEDIUM), liquidity_growth > 100% → "Suspicious Liquidity Growth" (MEDIUM). Each Insight has `importance` (LOW/MEDIUM/HIGH), `summary` (human-readable), `source_features` (traceability). | DOC-012 § B.4: Insights summarize Features. DOC-008: "Insights never become input to downstream pipelines." Deterministic rule ordering. |
| **Insight persistence** | **`insights` table in PostgreSQL** (DOC-014 § B.4). Alembic migration. Repository: `save_insight`, `list_insights_for_entity`, `get_latest_insight`. | DOC-014 § Storage Assignment: "Outcome, Insight (Part B.4) → PostgreSQL → outcomes, insights — regular tables, not hypertables." |
| **Cache strategy for GoPlus results** | **Redis with 24h TTL**, keyed by `goplus:{chain_id}:{address}`. JSON serialization of the raw GoPlus response. Fall back to API call on cache miss. | GoPlus contract security data is semi-static (rarely changes). 24h cache avoids redundant calls within the daily quota. Redis is already available. |
| **Latency budget** | **<5 seconds per pair** (filter scan + GoPlus API call + risk computation + insight generation). Integration test with timing assertion. | DoD: "within the same latency budget as its first Feature computation." Feature computation is <1s; GoPlus API call is typically 1-3s; 5s is conservative. |

---

## 2. Build Order (Sequential)

Gates: every step passes `make lint && make typecheck && make import-check` before the next begins.

### Phase A: Domain Layer (Schemas)

1. **`src/onchain_platform/domain/schemas/risk_signals.py`** — RiskSignals schema.
   - Frozen Pydantic model. Fields from GoPlus response (all `str` — GoPlus returns strings):
     - Contract security: `is_open_source`, `is_proxy`, `is_mintable`, `owner_address`, `can_take_back_ownership`, `owner_change_balance`, `hidden_owner`, `selfdestruct`, `external_call`
     - Trading security: `is_in_dex`, `buy_tax`, `sell_tax`, `transfer_tax`, `is_honeypot`, `cannot_buy`, `cannot_sell_all`, `transfer_pausable`, `is_blacklisted`, `is_whitelisted`, `slippage_modifiable`, `trading_cooldown`
     - Info security: `holder_count`, `total_supply`, `creator_address`, `creator_percent`, `owner_percent`, `is_airdrop_scam`, `trust_list`, `fake_token`, `other_potential_risks`
     - Computed: `risk_score: float` (0.0–1.0), `risk_indicators: list[str]`, `risk_rules_version: str`
   - Deps: none.
   - Verification: unit test round-trip, frozen-mutation rejection.
   - Complexity: moderate.

2. **`src/onchain_platform/domain/schemas/insight.py`** — Insight schema (DOC-012 § B.4).
   - Frozen Pydantic model. Fields: `schema_version`, `insight_id`, `entity_id`, `insight_type` (str, e.g. "HoneypotDetected"), `summary` (str, human-readable), `generated_at` (datetime), `source_features` (list[str]), `importance` (enum: LOW/MEDIUM/HIGH).
   - Validator: `insight_id` format validation.
   - Deps: none.
   - Verification: unit test round-trip, frozen-mutation rejection.
   - Complexity: trivial.

3. **`src/onchain_platform/domain/schemas/enums.py`** — add `Importance` enum.
   - `Importance(StrEnum)`: LOW, MEDIUM, HIGH (DOC-012 § B.4).
   - Deps: none.
   - Verification: unit test.
   - Complexity: trivial.

4. **`tests/unit/test_risk_schemas.py`** — unit tests for RiskSignals + Insight schemas.
   - Round-trip, frozen-mutation, risk_score range validation, importance enum.
   - Deps: steps 1–3.
   - Verification: `make test` green.
   - Complexity: trivial.

### Phase B: Persistence (Insights Storage)

5. **`src/onchain_platform/persistence/postgres/outcomes_insights.py`** — Insight ORM model + CRUD.
   - `InsightRow` per DOC-014: `insight_id` TEXT PK, `entity_id` TEXT, `insight_type` TEXT, `summary` TEXT, `generated_at` TIMESTAMPTZ, `source_features` TEXT[], `importance` native ENUM.
   - `save_insight(session, insight: Insight) -> bool` — upsert on `insight_id`.
   - `list_insights_for_entity(session, entity_id) -> list[Insight]` — ordered by `generated_at DESC`.
   - `get_latest_insight(session, entity_id, insight_type) -> Insight | None`.
   - Deps: step 2.
   - Verification: integration test against real Postgres.
   - Complexity: moderate.

6. **Alembic migration** — `insights` table.
   - Native ENUM for `importance`.
   - Index: `(entity_id, generated_at DESC)` — DOC-014 § Indexing Strategy.
   - Deps: step 5.
   - Verification: `make migrate` on fresh container.
   - Complexity: moderate.

### Phase C: GoPlus Client

7. **`src/onchain_platform/intelligence/goplus_client.py`** — async HTTPX client.
   - `GoPlusClient` class with:
     - `__init__(self, redis_client, base_url="https://api.gopluslabs.io", timeout=30.0)`
     - `async def get_token_security(self, chain_id: int, address: str) -> dict | None` — single-address query, cached in Redis (24h TTL), rate-limited (token bucket), exponential backoff on 429/5xx.
     - Token bucket: Redis key `goplus_rate_limit`, 140 tokens/min, refilled at 2.33 tokens/sec.
     - Daily CU tracking: Redis key `goplus_daily_cu:{YYYY-MM-DD}`, stop at 28,000.
     - All httpx errors → `AcquisitionError` (DOC-013 § Exception Hierarchy).
     - 30s timeout per call (DOC-013 § Async Conventions).
   - Deps: none (uses httpx, redis, domain.exceptions).
   - Verification: unit tests with mocked httpx (429, timeout, malformed JSON, success).
   - Complexity: high.

### Phase D: Filter Layer

8. **`src/onchain_platform/intelligence/filter.py`** — deterministic filter function.
   - `async def select_tokens_for_scan(session, redis_client, chain_id: int, current_block: int, max_tokens: int = 4500) -> list[str]` — returns list of pool_addresses to scan.
   - Filter rules (deterministic, no wall-clock):
     - Pool has reserves (reserve0 > 0 AND reserve1 > 0 in StateProjection — read from Redis).
     - Pool age ≤ 7 days (creation_block >= current_block - 7*24*1800 for Base ~2s blocks).
     - DEX is supported (dex IN ('uniswap_v2')).
     - Not already scanned in last 24h (Redis key `goplus_scanned:{chain_id}:{address}`).
     - Has at least 1 swap in last 24h (query blockchain_facts for recent SWAP_EXECUTED).
   - Sort by reserve depth proxy (reserve0 * reserve1) descending, take top N.
   - Deps: step 1 (for type references), persistence repositories, transport/state_cache.
   - Verification: unit tests with mocked data, quota assertion.
   - Complexity: moderate.

### Phase E: Risk Rules Engine

9. **`src/onchain_platform/intelligence/risk_rules.py`** — the core artifact.
   - `extract_risk_signals(goplus_response: dict) -> RiskSignals` — parse GoPlus JSON into RiskSignals.
   - `compute_risk_score(signals: RiskSignals) -> float` — deterministic weighted scoring (0.0–1.0).
   - `identify_risk_indicators(signals: RiskSignals) -> list[str]` — human-readable risk list.
   - All weights hardcoded, versioned (`RISK_RULES_VERSION = "1.0"`).
   - No wall-clock, no set iteration, deterministic rule ordering (DOC-013).
   - Deps: step 1.
   - Verification: unit tests with known GoPlus responses (honeypot, clean, high-tax, etc.).
   - Complexity: high.

### Phase F: Insight Generator

10. **`src/onchain_platform/intelligence/insight_generator.py`** — Insight generation.
    - `generate_insights(entity_id: str, risk_signals: RiskSignals, features: list[Feature] | None = None) -> list[Insight]`.
    - Rule-based mapping: risk_score → importance, specific indicators → insight_type + summary.
    - `source_features` populated from any Features used (traceability, DOC-012 § Traceability Chain).
    - Deps: steps 2, 9.
    - Verification: unit tests for each insight type.
    - Complexity: moderate.

### Phase G: Scheduler Integration

11. **`src/onchain_platform/intelligence/intelligence_job.py`** — APScheduler job function.
    - `async def run_intelligence_scan(pg_engine, redis_client, chain_id, clock)` — the actual job logic.
    - Calls filter → select tokens → for each: fetch GoPlus → extract signals → compute score → generate insights → persist.
    - Respects rate limits and daily quota.
    - Deps: steps 5, 7, 8, 9, 10.
    - Verification: unit test with mocked dependencies.
    - Complexity: moderate.

12. **`src/onchain_platform/main.py`** — wire intelligence job into scheduler (callback pattern).
    - Define `_run_intelligence` closure in main.py (composition root, exempt from contracts).
    - Register with APScheduler: `scheduler.add_job(_run_intelligence, "interval", minutes=5, ...)`.
    - Deps: step 11.
    - Verification: `make lint`, `make typecheck`, `make import-check` pass.
    - Complexity: trivial.

### Phase H: Integration Tests

13. **`tests/unit/test_risk_rules.py`** — unit tests for risk rules engine.
    - `test_honeypot_detected`: GoPlus response with `is_honeypot="1"` → score=1.0, indicator "Honeypot Detected".
    - `test_clean_token`: GoPlus response with all zeros → score≈0.0, no indicators.
    - `test_high_sell_tax`: `sell_tax="0.25"` → score includes sell_tax weight, indicator "High Sell Tax (25%)".
    - `test_hidden_owner`: `hidden_owner="1"` → score includes hidden_owner weight.
    - `test_deterministic`: same inputs → same outputs (100 iterations).
    - Deps: step 9.
    - Verification: `make test` green.
    - Complexity: moderate.

14. **`tests/unit/test_insight_generator.py`** — unit tests for insight generation.
    - `test_honeypot_insight`: honeypot risk → "Honeypot Detected" (HIGH).
    - `test_moderate_risk_insight`: score 0.6 → "Moderate Risk Detected" (MEDIUM).
    - `test_whale_concentration_insight`: holder concentration > 50% → "Whale Concentration" (MEDIUM).
    - Deps: step 10.
    - Verification: `make test` green.
    - Complexity: moderate.

15. **`tests/unit/test_goplus_client.py`** — unit tests for GoPlus client.
    - `test_successful_response`: mock httpx success → parsed dict.
    - `test_429_backoff`: mock 429 → retries with backoff.
    - `test_timeout`: mock timeout → AcquisitionError.
    - `test_cache_hit`: second call returns cached result.
    - `test_daily_quota_exceeded`: mock daily CU at limit → skip.
    - Deps: step 7.
    - Verification: `make test` green.
    - Complexity: moderate.

16. **`tests/integration/test_intelligence.py`** — integration tests.
    - `test_filter_selects_correct_tokens`: insert TradingPairs with varying liquidity, verify filter selects high-liquidity ones.
    - `test_end_to_end_risk_read`: mock GoPlus response → risk signals → insights persisted.
    - `test_latency_budget`: measure end-to-end time < 5s (mock GoPlus with 1s delay).
    - Deps: steps 5, 8, 9, 10.
    - Verification: `make test` green.
    - Complexity: moderate.

### Phase I: Final Gate

17. **Final gate + commit.**
    - `make lint && make typecheck && make import-check && make test && make test-replay` all green.
    - Update ImplementationPlan.md Milestone 7 DoD checkboxes.
    - Commit.

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GoPlus rate limiting (429) | High | Medium | Token bucket (140 CU/min) + daily quota (28,000) + exponential backoff (1s, 2s, 4s, max 3 retries). |
| GoPlus API down/timeout | Medium | Medium | 24h Redis cache + graceful degradation (skip insight generation, log WARNING, continue). |
| Monthly CU exhausted before month end | Medium | High | Daily quota = 4,500 tokens (150,000 / 30 days with margin). Track in Redis. |
| Filter too aggressive (few tokens) | Medium | Medium | Start with conservative thresholds (reserve > 0, age ≤ 7 days). Tune after observing real data. |
| Filter too permissive (over quota) | Medium | High | Hard cap at 4,500/day; sort by liquidity depth to prioritize high-value pairs. |
| Risk rules false positives | High | Low | Document rules clearly; mark as MVP baseline; iterate in future milestones. Version rules for reproducibility. |
| Insight generation non-deterministic | Medium | High | No wall-clock, no set iteration, deterministic rule ordering (DOC-013). Unit test: 100 iterations same output. |
| Latency budget exceeded | Medium | Medium | Cache GoPlus results (24h), async processing, parallel filter scan. |
| GoPlus returns malformed JSON | Low | Medium | Pydantic validation on RiskSignals; graceful degradation (log warning, skip). |
| Import-linter violation | Low | High | Use callback pattern (proven in M6); wire everything in main.py (composition root, exempt). |
| `projection_engine.py` lacks `liquidity_usd` | High | Medium | Filter uses `reserve0 * reserve1` as liquidity depth proxy instead. Document as known limitation; compute `liquidity_usd` in a future milestone. |
| GoPlus free tier field availability | Medium | Medium | Some fields (e.g., `lp_holders`) may be restricted in free tier. Test with real API call; fall back gracefully if field missing. |

---

## 4. Definition of Done Matrix

| DoD Item (ImplementationPlan § Milestone 7) | Verification Method | Automated? |
|---|---|---|
| Newly discovered pair gets risk read within latency budget | Integration test: mock GoPlus (1s delay), measure end-to-end < 5s | Yes |
| Filter selects ≤4,500 tokens/day | Unit test: mock 10,000 pairs, assert output ≤ 4,500 | Yes |
| Risk rules are deterministic | Unit test: same inputs → same outputs (100 iterations) | Yes |
| Insight generation is reproducible | Unit test: same risk signals → same Insights | Yes |
| Graceful degradation on API failure | Integration test: mock GoPlus timeout → no crash, log warning | Yes |
| GoPlus rate limiting respected | Integration test: mock 429 → verify backoff + retry | Yes |
| Monthly CU quota respected | Unit test: daily tracking stops at 28,000 | Yes |
| `lint-imports` still passes | `make import-check` | Yes |
| All quality gates green | `make lint && make typecheck && make test && make test-replay` | Yes |

---

## 5. Out-of-Scope Confirmation

Per ImplementationPlan § Milestone 7 and § What Not To Build Yet:

- [x] Machine Learning models — NOT built (Phase 4, DOC-005).
- [x] Honeypot detection from bytecode analysis — NOT built (buy from GoPlus per ADR-006 Principle 5).
- [x] Real-time alerting/notifications — NOT built (Phase 7+, DOC-005).
- [x] API endpoints for Insights — NOT built (M9).
- [x] Dashboard visualization of Insights — NOT built (M9).
- [x] Multi-chain support beyond Base/BNB/Ethereum — NOT built (MVP scope).
- [x] Paid GoPlus tier — NOT used (free tier only).
- [x] B20 Token fields — NOT used (require authentication, out of free tier scope).
- [x] Rug-pull Detection API — NOT used (separate endpoint, defer).
- [x] Malicious Address API — NOT used (separate endpoint, defer).
- [x] No Redis Streams integration — direct function call pattern continues.
- [x] No second chain beyond Base for active scanning (filter supports multi-chain but M7 scans Base only).

---

## 6. Questions / Blockers

Q1 (needs human): The filter uses `reserve0 * reserve1` as a liquidity depth proxy because `liquidity_usd` is not computed in M5. Should we compute `liquidity_usd` as part of M7 (requires a price oracle for the quote token), or accept the proxy and defer `liquidity_usd` to a future milestone? Recommendation: accept the proxy for M7 — it's deterministic and uses only data already available. Document it as a known limitation.

Q2 (design note): GoPlus's `holder_count` and `holders` array are useful for whale concentration analysis, but the free tier may restrict some fields. The risk rules should handle missing fields gracefully (treat as "unknown", not as "safe"). Unit tests must cover the missing-field case.

Q3 (needs human): The filter's "has at least 1 swap in last 24h" rule requires querying `blockchain_facts` for recent SWAP_EXECUTED events. This is a potentially expensive query on a large table. Should we add an index on `(fact_type, event_time)` for this query pattern, or use the existing `(chain_id, block_number)` index? Recommendation: use existing indexes and filter in application code for M7; add a dedicated index only if profiling shows it's needed.

Q4 (design note): The `intelligence/` package is allowed to import from `persistence/`, `transport/`, and `domain/` (per DOC-011 layers contract). It must NOT import from `analytics/` or `processing/` (those are higher layers). The insight generator uses Features as input — Features are queried from `persistence/timescale/repositories.py`, not from `analytics/feature_engine.py`. This respects the dependency graph.

Q5 (needs human): The `intelligence_job.py` function is wired into the scheduler via a callback in `main.py` (same pattern as M6's feature computation). The callback wraps the full scan logic (filter → GoPlus → risk → insights). Confirm this is acceptable, or should the intelligence job be a separate APScheduler instance?

No hard blockers: every Q above has a stated fallback that keeps M7 buildable today.
