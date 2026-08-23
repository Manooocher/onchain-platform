# Milestone 10 Execution Plan — Strategy (Candidate Ranking)

> **Milestone 10 Goal:** candidates are ranked, not just observed.
>
> **Definition of Done — and MVP done, per DOC-003's actual Exit Criteria:** you, the researcher, choose this platform over the old fragmented workflow (DOC-002) for investigating a newly launched pair, consistently, not as a novelty.
>
> This is **planning only**. No implementation code is written here. Milestone 10 is the intentionally small capstone — it proves the platform is *adoptable*, not that it grows.

---

## 0. Pre-Flight Status

Verified against the committed tree at `HEAD 8bd331b` (branch `master`, clean, pushed, 0 ahead of origin/master).

| # | Check | Status | Evidence |
|---|-------|--------|----------|
| 1 | M9 gates still pass | ✅ | `make lint` PASS (154 files), `make typecheck` PASS (96 files, 0 issues), `make import-check` PASS (8/8 KEPT), `make test` **239 passed** (1 order-dependent skip), `make test-replay` **7 passed** |
| 2 | `strategy/` exists but is empty | ✅ | contains only an empty `__init__.py` |
| 3 | `strategy/ranking.py` does NOT exist | ✅ | not present |
| 4 | M6 Features available | ✅ | `liquidity_growth_pct_1h`, `price_momentum_zscore_1h`; readers `get_feature_at`, `list_latest_features`, `list_feature_names` |
| 5 | M7 Intelligence available | ✅ | persisted `insights` (e.g. `HoneypotDetected`) via `get_latest_insight` |
| 6 | M8 Outcomes available | ✅ | `get_latest_outcome`, `list_outcomes_for_entity` |
| 7 | API endpoints available (M9) | ✅ | full DOC-015 surface; `create_app()` is a factory (no module-level `app`) |
| 8 | Ranking-input persistence readers exist | ✅ | `list_pairs`, `list_latest_features`, `get_latest_outcome`, `get_latest_insight` all present |

### ⚠️ Critical architectural constraint (must resolve before building)

**DOC-011 § Enforcing the Dependency Rule has an asymmetric Strategy↔Research contract:**

- `research/` may **NOT** import `strategy/` (research forbidden list includes `onchain_platform.strategy`).
- `strategy/` `forbidden_modules` lists `acquisition, processing, domain_management, analytics, intelligence` — but **NOT `research`**, and `research` sits *below* `strategy` in the `layers` contract.
- **Therefore: `strategy/` MAY import `research/` (e.g. `research.api.deps`, `research.api.schemas`), but `research/` may NOT import `strategy/`.**

**Consequence for the API endpoint `GET /v1/strategy/rankings`:**
A router file placed at `research/api/routes/strategy.py` would import `strategy/ranking.py` — that is a **research→strategy import, a contract violation**. The endpoint must instead be **owned by the `strategy/` package itself**: a `strategy/api.py` that builds an `APIRouter` importing only from `research.api.deps` / `research.api.schemas` (allowed, since strategy may import research) + `strategy/ranking.py` (same package). Then the composition root wires it.

**Who calls `create_app()`?** `research/api/main.py` exposes only `create_app()` (no module-level app; DOC-015 has no strategy endpoint). Two clean wiring options:
- **(a) Optional router injection:** `create_app(extra_router: APIRouter | None = None)` — `main.py` (exempt composition root) builds the strategy router and passes it. Existing tests calling `create_app()` unchanged (strategy endpoint absent unless injected — the strategy integration test passes the router explicitly).
- **(b) `strategy/api.py` self-contained:** expose `build_strategy_router()` that the API layer mounts through an injected hook.

**Recommendation: (a)** — least invasive, keeps `create_app()` backward-compatible, and matches the established "main.py is the only file allowed to see two Capabilities at once" composition rule. A NEW strategy router is added only when `main.py` (or a test) passes it.

This is the single most important decision in the milestone and must not be skipped.

---

## 1. Open Decisions — Resolved

| # | Decision | Resolution | Rationale |
|---|----------|-----------|-----------|
| D1 | **Ranking inputs** | **(b) Features + Risk signals first; add Outcomes when data is dense enough.** Start with Features (from Timescale, PIT via `list_latest_features`) + a risk penalty (from `get_latest_insight` for `HoneypotDetected`-type, or a stored risk score if present). Outcomes are incorporated as a reviewable **penalty/boost factor only when a `get_latest_outcome` exists** — the vast majority of live pairs have no closed outcome window (M8), so it cannot be the ranking's backbone. | DOC-009: Strategy "recommends"; determinism + explainability are the DV. Features are dense (M6), Outcomes are sparse (M8) — a backbone on sparse data yields unstable ranks. Document the limitation. |
| D2 | **Ranking algorithm** | **Deterministic weighted sum** over explicit, versioned factors, with thresholds in a separate `ranking_config` (Python constants, not YAML — avoids a new dependency; DOC-010 already has no YAML config need beyond confirmation_depth). Each factor is a normalized sub-score in a bounded range, summed. | DOC-009 "rule-based"; DOC-013 Determinism Discipline (no wall-clock, no randomness, same inputs → same outputs). Weighted sum is the most explainable deterministic form. |
| D3 | **Ranking output** | **(c) pair ID + score + per-factor contributions** — return `RankedCandidate {pair, score, factors: {feature_name: contribution, risk_penalty, outcome_signal, ...}}`. | DOC-001 "Explainable" is non-negotiable; a researcher must see *why* a pair ranked where it did. |
| D4 | **API endpoint** | **Yes: `GET /v1/strategy/rankings`**, with query params `chain_id`, `dex`, `limit`, and an optional `as_of`. Owned by `strategy/` package (see §0 architecture note), not by `research/api/routes/`. | DOC-015 has no strategy endpoint; this is a new M10 addition. Because research cannot import strategy, the router lives with the capability that owns the ranking. |
| D5 | **Dashboard page** | **Yes: a minimal "Top Candidates" table page** (`research/dashboard/pages/4_top_candidates.py`) showing pair id + score + top factors. HTTPX-only, like all pages. | DOC-003 Exit Criterion is *adoption* — a researcher must see the ranking in the same UI they already use. Keep it a table, no charts. |
| D6 | **Scheduling** | **on-demand + optional hourly batch.** The ranking is cheap (reads latest-per-name features for a bounded pair set). Add an APScheduler job only if a live dashboard needs it; for MVP, compute lazily on the API call. | DOC-004 "optimize after a real bottleneck," not before. Hourly aligned with feature/outcome cadence is the *target interval* if batch is added. |
| D7 | **Explainability** | `ranking_factors` dict on each `RankedCandidate`: for every feature used (e.g. `liquidity_growth_pct_1h`, `price_momentum_zscore_1h`) store its normalized sub-score + weight; include `risk_penalty` and (when present) `outcome_signal`. | Make "why" query-able in the same HTTP round trip — the standing DOC-001 requirement. |
| D8 | **Where the ranking lives** | `strategy/ranking.py` for the pure function; `strategy/api.py` for the APIRouter that imports `research.api.deps` (allowed) + `strategy/ranking.py`. `main.py` injects it into `create_app(extra_router=...)`. | Respects the asymmetric import contract (§8). |

---

## 2. Build Order (Sequential)

Strict dependency order; one phase = one commit with green gates. Do NOT proceed past a failing gate.

### Phase A — Strategy Domain/Schema
1. **`src/onchain_platform/domain/schemas/ranking.py`** (new) — `RankingFactor` (name, value, weight, contribution) and `RankedCandidate` (pair_id, score, factors: list[RankingFactor], rank). Frozen Pydantic, no confidence / no override-of-outcome semantics.
2. **No new enums needed** — `domain/schemas/enums.py` unchanged (ranking adds no enum monetary type).
3. **`tests/unit/test_ranking_schema.py`** — round-trip, frozen-mutation rejection, factor list non-empty.
   - **Gate A** green. Commit `feat(strategy): add ranking schema`.

### Phase B — Ranking Engine (pure, deterministic)
4. **`src/onchain_platform/strategy/ranking_config.py`** (new) — documented constants: `WEIGHTS: dict[str, float]`, `NORMALIZE_CAP`, `MIN_FEATURES_REQUIRED`, `RISK_PENALTY`, `OUTCOME_BOOST`. Pure data (DOC-001 relevance), no I/O.
   - Feature set referenced: `liquidity_growth_pct_1h`, `price_momentum_zscore_1h` (+ any future suffix-typed names).
   - **Deterministic**: no wall-clock, no randomness (DOC-013).
5. **`src/onchain_platform/strategy/ranking.py`** (new) — `async def compute_ranking(session, *, chain_id=None, dex=None, limit=50, as_of=None) -> list[RankedCandidate]`:
   - `list_pairs(chain_id, dex, ...)` → candidate base.
   - For each pair, `list_latest_features(entity_id, as_of)` (PIT) — only features with names in the config's known set are used.
   - Risk penalty from `get_latest_insight(entity_id, HoneypotDetected)`-style; outcome signal from `get_latest_outcome(entity_id, ...)` when present.
   - Normalize each raw feature into a 0..1 sub-score; sum weighted contributions → total score; sort desc; attach `factors`.
   - **No analytics/intelligence imports** — reads only via `persistence/` (cross-cutting) + `domain/`. Deterministic (sorted list, no set iteration).
6. **`tests/unit/test_ranking_rules.py`** — same inputs → same outputs (determinism), weight boundary, missing-feature handling (a pair with no features is rankless / dropped or scored zero per config), risk penalty applied.
   - **Gate B** green. Commit `feat(strategy): add deterministic ranking engine`.

### Phase C — Strategy Router + API Endpoint
7. **`src/onchain_platform/strategy/api.py`** (new) — `build_strategy_router()` returns an `APIRouter` with `GET /v1/strategy/rankings`:
   - Imports `compute_ranking` (strategy) + `get_session` (from `research.api.deps`, allowed) + `RankedCandidate` (domain).
   - Query params `chain_id`, `dex`, `limit`, `as_of` (optional).
   - Response `list[RankedCandidate]`, `summary`/`description` per DOC-015 § OpenAPI.
   - **router is owned by the strategy package** — no research→strategy import anywhere.
8. **`src/onchain_platform/research/api/main.py`** (edit) — extend `create_app(extra_router: APIRouter | None = None)`; if given, `app.include_router(extra_router, prefix="/v1")`.
9. **Integration tests** `tests/integration/test_strategy_api.py` — build `create_app(extra_router=build_strategy_router())`, hit `/v1/strategy/rankings` with seeded pairs/features, assert 200 sorted-by-score, `factors` populated, deterministic on two calls.
   - **Gate C**: `make import-check` still **8/8 KEPT** (critical — proves no research→strategy edge). Commit.

### Phase D — Dashboard Page
10. **`src/onchain_platform/research/dashboard/pages/4_top_candidates.py`** — minimal table: per row `rank`, `pair_id`, `score`, `factors`. **HTTP-only** via `api_client` (add `get_rankings()` to `src/onchain_platform/research/dashboard/api_client.py`).
11. **`src/onchain_platform/research/dashboard/app.py`** — add "Top Candidates" to the nav.
12. **Manual smoke** — run API + dashboard locally; confirm the rankings table renders (feature-engineered seed data only; live DB may be sparse).
   - **Gate D** green. Commit `feat(research/dashboard): add Top Candidates page`.

### Phase E — Final Gate + MVP DoD
13. **`make lint typecheck import-check test test-replay`** + `pytest -m live` — all green; **import-linter 8/8** (verify strategy never imports analytics/intelligence; research never imports strategy).
14. **`docs/implementation/ImplementationPlan.md`** — append the M10 DoD "✅ Verified" line (M4–M9 convention) marking the **MVP exit criterion met** (DOC-003).
15. **Final commit + push** to `origin/master`.

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| No outcome data for inclusion | High | Medium | Backbone = Features + risk penalty; Outcomes are a sparse, documented signal (D1). Never produce garbage when a pair has no closed outcome. |
| Ranking yields meaningless orders | Medium | High | Weighted normalized sub-scores in bounded 0..1 range; integration test with seeded controlled features asserts monotonic ordering + values. |
| Import-linter violation (strategy→analytics/intelligence, or research→strategy) | Low | High | The router is owned by `strategy/`; `ranking.py` imports only `persistence/` + `domain/` + `research.api.deps`. `make import-check` after every phase. |
| `outcome` sparse + `feature` sparse pair set → empty result | Medium | Low | If `MIN_FEATURES_REQUIRED` unmet for all pairs, ranking returns `[]` gracefully (an empty research list), not an error. Dashboard renders "none". |
| Dashboard / nav complexity creep | Low | Low | Keep the page a plain table; no charts. Each dashboard page remains HTTP-only. |
| `as_of` default ambiguity | Low | Low | If omitted, `list_latest_features`/`get_latest_insight` default to their own `as_of` (current-time at call). Document. |

---

## 4. Definition of Done Verification Matrix

| DoD Item | Verification Method | Automated? |
|----------|--------------------|------------|
| Candidates are ranked (not just observed) | Integration: `GET /v1/strategy/rankings` returns a list sorted by score desc | Yes |
| Ranking is deterministic | Unit: same seeded inputs → identical output on repeated calls | Yes |
| Ranking is explainable | Unit + API: every `RankedCandidate.factors` is non-empty and each factor has name/value/weight/contribution | Yes |
| Outcome signal incorporated (when present) | Integration: a pair with a closed positive outcome ranks higher than an identical pair without (only when a boost is enabled) | Yes |
| Risk penalty applied | Unit: a pair with `HoneypotDetected` insight is penalized | Yes |
| Dashboard shows rankings | Manual smoke (table renders; HTTP-only proven by existing AST test) | No |
| Research does NOT import strategy | `make import-check` stays 8/8 KEPT | Yes |
| All gates green | `make lint / typecheck / import-check / test / test-replay` + live smoke | Yes |

---

## 5. Out-of-Scope Confirmation

Milestone 10 does **NOT** include:
- [ ] Trade execution (DOC-003 Non-Goal)
- [ ] Portfolio management (DOC-003 Non-Goal)
- [ ] Notifications / alerts (DOC-003 Non-Goal)
- [ ] Copy trading (DOC-003 Non-Goal)
- [ ] Machine-learning ranking (DOC-009 Future Capability — Strategy maturity = "Rule-based" for MVP)
- [ ] Reinforcement learning (DOC-003 Non-Goal)
- [ ] Autonomous agents (DOC-003 Non-Goal)
- [ ] Multi-chain support (MVP is EVM-first only)
- [ ] Real-time ranking updates (batch/on-demand computation is sufficient)
- [ ] New persisted canonical schema or DB table (ranking is a derived read, not stored) — unless a brainstorm requirement changes it
- [ ] Extending DOC-015's endpoint catalog in the doc (the new `/v1/strategy/rankings` is called out in the M10 plan, not retrofitted into B.0–B.5)

---

## 6. Questions / Blockers

**Q1 (BLOCKING — needs decision):** The `strategy/` → `research/` import direction (allowed) means the `/v1/strategy/rankings` **router is owned by `strategy/api.py`**, not by `research/api/routes/`, and is injected into `create_app(extra_router=...)` from `main.py`. Confirm this composition-root injection is acceptable (it follows the established scheduler-callback pattern from M6/M7/M8), or whether you prefer a `strategy`-owned sub-app mounted differently. **Recommendation: injection (a).**

**Q2 (BLOCKING):** Should ranking **persist** its output (a `rankings` table) or be computed lazily on each `/v1/strategy/rankings` call? Recommendation: **lazy, no new table** — the ranking is cheap (latest-features per pair) and lazily computed is simpler; a batch/persisted snapshot is a later optimization only if latency proves real (DOC-004).

**Q3 (design):** What is the minimum viable threshold for the ranking? Currently the plan returns the full sorted subset (bounded by `limit`), including possibly-weak candidates. Confirm that a **soft threshold** (e.g. `MIN_SCORE` config) should be applied to drop noise-only candidates, or leave it to the researcher to eyeball the full top-N. Recommendation: leave full top-N (bounded by limit), let the researcher filter later.

**Q4 (naming):** The new `strategy/api.py` (router) + `research/api/main.py` `extra_router` parameter names — confirm `build_strategy_router()` naming is acceptable, and that `extra_router` is a safe API addition.

**Q5 (maintainability):** The `ranking_config.py` — Python constants vs YAML? Project has no YAML dependency beyond `confirmation_depth.yaml` (path-read). Recommendation: **Python constants** (a new `.py` file), documented, to avoid a new dependency and keep determinism explicit.

**NEXT:** After sign-off on D1–D8 + Q1–Q5, **Phase A** (rank schema) begins. Do not start Phase B until Phase A's nested-gate is green.