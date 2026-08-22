# Milestone 9 Execution Plan — Research Platform (API & Dashboard)

> **Milestone 9 Goal:** a human — or an agent — can actually ask the platform a question instead of querying Postgres by hand.
>
> **Definition of Done:** DOC-002's own success definition, tested for real — answer the example question *"why did this token gain momentum"* using only the API, not a database client.
>
> This is **planning only**. No implementation code is written here. DOC-015 is the authoritative "what" — it is unusually prescriptive; this plan resolves the "how", the build order, and the persistence-layer prerequisites it silently assumes. Verify every artifact before starting.

---

## 0. Pre-Flight Status

Verified against the committed tree at `HEAD e6c9498` (branch `master`, working tree clean **except** one pre-existing uncommitted change — see issue 2 below).

| # | Check | Status | Evidence |
|---|-------|--------|----------|
| 1 | M8 gates still pass | ✅ | `make lint` PASS (121 files formatted), `make typecheck` PASS (72 files, 0 issues), `make import-check` PASS (8/8 KEPT), `make test` **197 passed**, `make test-replay` **7 passed** |
| 2 | `research/api/` empty scaffolding only | ✅ | contains only empty `__init__.py` |
| 3 | `research/dashboard/` empty scaffolding only | ✅ | contains only empty `__init__.py` |
| 4 | FastAPI in dependencies | ✅ | `pyproject.toml`: `fastapi>=0.139.0`, `uvicorn>=0.51.0` |
| 5 | Streamlit in dependencies | ✅ | `pyproject.toml`: `streamlit>=1.59.2` |
| 6 | HTTPX in dependencies (for dashboard client + API tests) | ✅ | `httpx[socks]>=0.28.1` |
| 7 | Canonical Schemas / Entities exist | ✅ | All 8 response models present: `domain/entities/{blockchain,token,trading_pair,liquidity_pool,wallet,smart_contract,metadata}.py`; `domain/schemas/{blockchain_fact,market_bar,observation_snapshot,feature,insight,outcome,risk_signals,state_projection,chain_reorg_event,checkpoint}.py` |
| 8 | **All repository functions exist (pre-flight assumption = FALSE)** | ❌ **gaps found** | See "Persistence-layer gaps" below |
| 9 | No existing API code to conflict with | ✅ | `tests/integration/` has no API tests; no `api_client.py` |

### ⚠️ Pre-flight blocker/notes (must resolve before/while building)

**P1 — Uncommitted M8 doc change on disk.** `docs/implementation/ImplementationPlan.md` shows `1 insertion, 1 deletion` uncommitted (the M8 DoD verification line from the prior session was never committed). HEAD == origin/master (0 ahead), so `master` as pushed does **not** contain the M8 "✅ Verified" line. **Decision: fold the uncommitted M8 doc line into the first M9 doc commit** (commit it as part of Phase F docs, or as a tiny standalone `docs:` commit at the start). Do NOT silently drop it.

**P2 — Harden-tested `research/` import contract (DOC-011).** `research/` may import `analytics`, `intelligence`, + cross-cutting `persistence`, `transport`, `platform`; it is `forbidden` from `acquisition`, `processing`, `domain_management`, `strategy`. Everything in this plan respects that — the API reads **only** through `persistence/{postgres,timescale}/repositories.py`, never `processing`/`acquisition`.

### Persistence Gap (critical planning discovery)

DOC-015's endpoint catalog requires query patterns that **do not exist yet** in the repository layer. `persistence/postgres/repositories.py`, `entity_repositories.py`, `outcomes_insights.py`, and `timescale/repositories.py` currently expose:

- Single-reads: `get_fact`, `get_trading_pair`, `get_token`, `get_wallet`, `get_bar`, `get_latest_snapshot`, `get_feature_at`, `get_latest_insight`, `get_latest_outcome`.
- Collections: `list_facts_for_chain`, `list_all_trading_pairs`, `list_pairs_for_token`, `list_insights_for_entity`, `list_outcomes_for_entity`, `list_bars`, `list_snapshots`, `list_features`.
- `save_*` for everything (write side is complete; read side for the API is partial).

**Missing for the API (must be added — this is new M9 work, not existing surface):**
1. **List trading pairs with filters + cursor** — `list_pairs(session, *, chain_id=None, dex=None, created_after=None, limit, cursor_keys)`. `list_all_trading_pairs` is a coarse iteration (returns all, no filter/limit/keyset) and is the only list-like. Need a keyset-paginated, filtered variant.
2. **Nested-entity reads** — `get_liquidity_pool` (+ maybe `list_liquidity_pool_by_pair`), `get_smart_contract` (needed to nest in `/tokens/{id}`), `get_metadata` (needed to nest in `/pairs/{id}` and `/tokens/{id}`). Today only `save_liquidity_pool/smart_contract/metadata` exist — **no readers**.
3. **Facts by pair** — `GET /pairs/{id}/facts` needs facts filtered by `pair_id`. `blockchain_facts` has NO pair_id column; the fact table stores `payload.pool_address` (SWAP/LIQ) or `payload.pair_address` (PAIR_CREATED). Must add `list_facts_for_pair(session, chain_id, pair_address, *, fact_type, start, end, include_unfinalized, limit, cursor_keys)` filtering on `payload->>'pool_address'`/`payload->>'pair_address'`. **This is a JSONB-filtered keyset query — page ordering key must be `(block_number, log_index)` or `fact_id`.**
4. **Facts by wallet** — `GET /wallets/{id}/activity` needs facts by `involved_wallets` (the GIN column, DOC-014). Must add `list_facts_for_wallet(session, wallet_address, start/end, limit, cursor_keys)` using the existing `ix_blockchain_facts_involved_wallets` GIN index + filter on `confirmation_status != ORPHANED` semantics per `include_*`.
5. **PIT "all features"** — `GET /entities/{id}/features` needs a **latest-per-feature_name** query (one index seek per distinct feature_name, per DOC-015). `get_feature_at` covers single-name; need `list_latest_features(session, entity_id, as_of)`.
6. **Paged insights** — `list_insights_for_entity` exists but has no `start/end/insight_type/limit/cursor`; need a paged variant `list_insights_page`.
7. **Paged outcomes** — `list_outcomes_for_entity(entity_id, outcome_type=None)` exists but no cursor/limit; need `list_outcomes_page`.
8. **Paged snapshots / bars / datasets** — `list_snapshots`/`list_bars` have `from/to` but **no limit/cursor**; `list_bars` requires a single `interval`. The dataset endpoint needs `list_bars` over the (filtered, single-interval) range + `list_features` (range + name list) + `list_outcomes` for a range. Need small cursor-aware wrappers or keyset fetchers.
9. **Unique `feature_name` list** — for `GET /pairs/{id}/dataset` optional `feature_names`, and for the dashboard, need `list_feature_names(session, entity_id)`.

**This is the single biggest "the pre-flight claimed it exists, it doesn't" finding.** It means the build has two layers under the routers: persistence-read-first, then the HTTP surface. The plan sequence below makes the persistence read layer a first-class Phase A.

---

## 1. Open Decisions — Resolved

| # | Decision | Resolution | Rationale |
|---|----------|-----------|-----------|
| D1 | **API file structure** | Split by resource, one file per resource group, in `research/api/routes/` (pairs.py, tokens.py, wallets.py, facts.py, bars.py, snapshots.py, insights.py, outcomes.py, features.py, dataset.py), each containing only route functions; `research/api/main.py` holds the FastAPI app factory `create_app()` that mounts them. | Matches the resource model (DOC-015 § Resource Model) 1:1; keeps routers small; `main.py` stays wiring-only (DOC-011 composition-root spirit). Single `import-path` per resource avoids a 700-line router. |
| D2 | **Pagination cursor encoding** | Base64-URL-encoded JSON of the last row's ordering key, e.g. `{"fact_id": "8453:0x...:7"}` or `{"bar_start_time": "2026-06-01T00:00:00+00:00"}`. Encode via `urlsafe_b64encode(json.dumps(...))`; decode with validation + try/except → 422 on malformed/missing key. | Stable across concurrent appends (DOC-015 § Response Shape). Keys are unique/`|,`-free natural keys per resource; base64url avoids `/`/`+` in URL escaping. A decode failure is a client bug → 422, not 500. |
| D3 | **PIT query default** | If `as_of` omitted, use `datetime.now(UTC)` at query time inside the router (the API layer is allowed the wall clock — DOC-013 carve-outs are for Capability pipeline logic, and DOC-015 § PIT explicitly says "defaults to the current server time"). The underlying repository reads never read the clock. | DOC-015 precise language; the router is a boundary where "now" is a legitimate query default. |
| D4 | **Dataset assembly** | `asyncio.gather` over: (1) `bars` for range+interval, (2) `features` for range limited to `feature_names` (or all), (3) `outcomes` for range, (4) `pair` manifest. Assemble the DOC-015 response dict of Canonical Schemas; **bars `items` filtered/sorted by bar_start_time; features as a `list[Feature]`; outcomes as `list[Outcome]`; no pagination envelope** (range bound is the size control). Enforce `end - start <= 90 days` (return 422 if exceeded). | DOC-015 § The Research Dataset Assembly: parallel is cheap on separate hypertables; explicit range + 90d cap prevents the "space heater" join; features stay vertical (fidelity to DOC-012), never pivoted. |
| D5 | **Streamlit scope** | Minimal 3-page dashboard: `1 Pairs List` (filters by chain_id/dex, choose pair), `2 Pair Detail` (pair metadata + bars line chart + latest features), `3 Dataset Explorer` (interval/start/end/features → shows assembled dataset table + raw JSON). Defer charts beyond bars, real-time, auth, multi-user. | Bill-of-material sized to prove DOC-015 works end-to-end, not a full research IDE. Follows DOC-005 "walking skeleton" discipline. |
| D6 | **Dashboard data source** | **HTTPX only** — `research/dashboard/api_client.py` (a thin typed client wrapping httpx against the FastAPI base URL). The dashboard NEVER imports `persistence/`, `domain/entities`, or `analytics/` directly. | DOC-015 §6.Dashboard: "never a second data path". The API is the only data access. |
| D7 | **CORS** | `CORSMiddleware(allow_origins=["http://localhost:8501"], allow_methods=["GET"], allow_headers=["*"])`. Streamlit default port is 8501. | DOC-015 §7 Security: CORS open *for local dev*, scoped to localhost. Allow only GET (API is read-only). 8501 matches Streamlit. |
| D8 | **Authentication** | **None in the MVP** — explicitly documented, no code. | DOC-015 pillar 2 + DOC-010 § Security. Trigger for adding it is the Research Platform reaching DOC-009 Phase 2/3 (multi-user) — deferred, not a gap. |
| D9 | **OpenAPI at `/v1/openapi.json`** | A route `app.get("/v1/openapi.json")` returning `app.openapi()`. FastAPI's built-in `/openapi.json` is auto-generated too; we add the `/v1/` alias. | DOC-015 §8: the doc is served, not hidden behind the interactive UI. |
| D10 | **Error correlation IDs** | A `@app.middleware("http")` that creates a `correlation_id = uuid4().hex` per request, stores it on `request.state`, injects it into EVERY error response body `{error:{code,message,correlation_id}}`, and binds it to the request's structlog context. | per DOC-015 §4 (every error body has `correlation_id`). |
| D11 | **Error mapping** | A `PlatformError`/`PersistenceError` handler → 500 with `code=PERSISTENCE_ERROR`/`PLATFORM_ERROR`; a missing resource resolved as a 404 (`code=RESOURCE_NOT_FOUND`); `ValidationError` → FastAPI's default 422. Missing single-resource GET → **404**, not empty 200 (per DOC-015 § PIT / Resource Model). | per DOC-015 §4 Error Handling table (422/404/500) exactly; `code` strings are stable, deterministic upper-case snake_case values per that table. |
| D12 | **Response models = Canonical Schemas** (no DTOs) | Routers declare `response_model=TradingPair`, `list[MarketBar]`, etc., importing directly from `domain/`. Collection endpoints return a `PaginationEnvelope` Pydantic model `{items, pagination:{next_cursor,has_more}}`. | DOC-015 §3: a response body is a Canonical Schema serialized; no bespoke DTO. Financial fields are already `str` in schema → serialize as strings automatically, non-negotiable. |
| D13 | **Status / invariant 2 in blockchain facts** | For facts routes, `include_unfinalized` defaults false; only `FINALIZED`(+`CONFIRMED`?) rows kept per flag. Because data is append+status-churn, the keyset cursor must be **`fact_id`**, and the query must tolerate rows changing status mid-pagination (a status filter on an append-only value). Handle minimal (no streaming); a status-filtered page is stable enough for research use. | DOC-015 explicitly requires `include_*` filters. |

---

## 2. Build Order (Sequential)

> One phase = one commit with green gates; do not proceed to the next phase until the current gate passes. Phase 0 is a permitted addition the task's pre-flight assumed already existed (it did not — this is the persistence-read gap).

### Phase 0 — Persistence Read Layer (foundation for every endpoint)
1. **`persistence/postgres/entity_repositories.py`** — add readers: `get_liquidity_pool(session, canonical_id)`, `get_smart_contract(session, canonical_id)`, `get_metadata(session, entity_id|canonical_id)`. (Only `save_*` exists today — DOC-015 `/pairs/{id}` + `/tokens/{id}` need these to nest.) All wrap `SQLAlchemyError → PersistenceError` (DOC-013).
2. **`persistence/postgres/repositories.py`** — add paged/filtered readers:
   - `list_pairs(session, *, chain_id=None, dex=None, created_after=None, cursor=None, limit=100)` → keyset on `(chain_id, creation_block, canonical_id)` returns `(items, next_cursor)`. Replaces/augments `list_all_trading_pairs`.
   - `list_facts_for_pair(session, chain_id, pair_address, *, fact_type=None, start=None, end=None, include_unfinalized=False, cursor=None, limit=100)` — JSONB filter on payload `pool_address`/`pair_address`, ordered by `fact_id`.
   - `list_facts_for_wallet(session, chain_id, wallet_address, *, start, end, cursor, limit)` — uses GIN index `ix_blockchain_facts_involved_wallets`.
   - `get_fact(already exists)` (verify reuse).
3. **`persistence/postgres/outcomes_insights.py`** — add paged `list_outcomes_for_entity_page(..., outcome_type, cursor, limit)` and `list_insights_for_entity_page(session, entity_id, *, start=None, end=None, insight_type=None, cursor=None, limit=100)`.
4. **`persistence/timescale/repositories.py`** — add:
   - `list_bars_page(session, pair_id, interval, *, start, end, include_provisional, cursor=None, limit=100)` (preserve `list_bars` for datasets).
   - `list_snapshots_page(session, entity_id, *, start, end, cursor, limit)` (keyset on `snapshot_timestamp`).
   - `list_features_page(session, entity_id, *, feature_names=None, start, end, cursor, limit)`.
   - `list_latest_features(session, entity_id, as_of)` → latest-per-name (single query, one seek per distinct name — DOC-015 § PIT "all features").
   - `list_feature_names(session, entity_id)`.
5. **Integration tests** for every new reader against real Postgres: keyset next-cursor correctness, filters, `include_unfinalized` semantics, empty pages, cursor stability across an appended row.
   - **Gate 0:** lint/typecheck/import-check pass; `make test` runs old + new; note this is a large phase — run `make test-replay` too (repro pipelines stay green).
   - **Commit 0** `feat(persistence): add read API query layer (paged keysets, entity readers)`.

### Phase A — API Foundation (app factory, deps, pagination, middleware)
6. **`research/api/pagination.py`** — `encode_cursor(keys: dict) -> str`, `decode_cursor(cursor: str) -> dict` (raise `InvalidCursor`), unit tests (round-trip, malformed→422).
7. **`research/api/deps.py`** — `get_settings()` (lazy `Settings()`), `get_session()` → async `AsyncSession` from engine in settings.
8. **`research/api/errors.py`** — `PlatformErrorHandler` middleware mapping PlatformError→500 with `{error:{code,message,correlation_id}}`; `correlation_id` via `uuid4().hex` in `request.state` + structlog bind.
9. **`research/api/main.py`** — `create_app()`:
   - `CORSMiddleware` (localhost:8501, GET only),
   - register routers,
   - `/v1/openapi.json` route,
   - `/v1/health` (returns `{status:"ok", version, commit_hash}` — liveness; needs no DB to prove the process is up, optionally pings DB), 
   - `PlatformError`/`PersistenceError` handlers.
10. **pyproject.toml** — nothing new (FastAPI/Uvicorn already present). `fastapi.testclient` uses httpx (already a dependency), so no extra test dep is needed.
11. **Unit/integration tests** — `TestClient(create_app())` hitting `/v1/health` and `/v1/openapi.json`; assert CORS header present; error handler emits `correlation_id`.
   - **Gate A:** gates green; `/v1/health` + `/v1/openapi.json` return 200 with correct body. Commit.

### Phase B — Entity & Pair Index (single-resource endpoints)
12. **`research/api/routes/pairs.py`** — 
   - `GET /v1/pairs` (list; filters chain_id, dex, created_after; paginated envelope),
   - `GET /v1/pairs/{canonical_id}` (pair + nested liquidity_pool + metadata).
13. **`research/api/routes/tokens.py`** — `GET /v1/tokens/{id}` (token + nested smart_contract + metadata).
14. **`research/api/routes/wallets.py`** — `GET /v1/wallets/{id}` (wallet), `GET /v1/wallets/{id}/activity` (paged facts via wallet GIN).
15. **Integration tests** (real Postgres): pair list filters, 404 on missing pair/token/wallet, nested liquidity_pool/metadata inclusion, wallet-activity pagination.
   - **Gate B:** gates green; manual `curl /v1/pairs` returns a paginated envelope.
**Commit B** `feat(research/api): pair/token/wallet endpoints with nested entities`.

### Phase C — Collection Endpoints w/ Cursor Pagination
16. **`research/api/routes/facts.py`** — `GET /v1/facts/{fact_id}` (single), `GET /v1/pairs/{id}/facts` (filtered, cursor).
17. **`research/api/routes/bars.py`** — `GET /v1/pairs/{id}/bars` (`interval` required as Enum, `start`/`end`, `include_provisional` (default false), cursor).
18. **`research/api/routes/snapshots.py`** — `GET /entities/{id}/snapshots` (cursor).
19. **`research/api/routes/insights.py`** — `GET /entities/{id}/insights` (cursor, `insight_type` optional).
20. **`research/api/routes/outcomes.py`** — `GET /entities/{id}/outcomes` (cursor, `outcome_type` optional).
21. **Integration tests** — for each: hit first page, follow `next_cursor`, assert no dupes/gaps, empty-entity → empty. Verify cursor stability if a row is appended between pages.
   - **Gate C (complete):** all pagination endpoints pass cursors — duplicates = 0, gaps = 0.
**Commit C** `feat(research): add facts/bars/snapshots/insights/outcomes routes with cursor pagination`.

### Phase D — Point-in-Time Feature Endpoints
22. **`research/api/routes/features.py`** — `GET /entities/{id}/features/{name}?as_of=...` (single: `get_feature_at`; 404 if none satisfies), `GET /entities/{id}/features?as_of=...` (multi-latest-per-name).
23. **Integration tests** — PIT correctness verified: same `as_of` → identical body (query twice), different `as_of` → different valid-time result; a row created AFTER `as_of` is excluded (no lookahead).
   - **Gate D:** PIT suite passes.
**Commit D** `feat(research): PIT feature endpoints`

### Phase E — Dataset Assembly
24. **`research/api/routes/dataset.py`** — `GET /pairs/{id}/dataset?interval=..&start=..&end=..&feature_names=a,b`; validate `interval in Enum`, `end-start <=90d` (422 otherwise), `asyncio.gather` three queries, assemble per D4.
25. **Integration test** — seed pair+snapshots+bars+features+outcomes, GET dataset, assert exact response **contracts** per DOC-015 example (pair object + bars + features + outcomes; `outcomes` `[]` when none; features vertical array).
   - **Gate E** green.
   - **Commit E.**

### Phase F — Streamlit Dashboard
26. **`research/dashboard/api_client.py`** — typed HTTPX client (`get_pairs`, `get_pair`, `get_bars`, `get_features`, `get_features_by_name`, `get_dataset`). Uses `asyncio.run` to bridge async httpx into Streamlit's sync context.
27. **`research/dashboard/app.py`** — Streamlit multi-page (sidebar: `1_`, `2_`, `3_` pages).
28. **`research/dashboard/pages/1_pairs_list.py`**, `2_pair_detail.py`, `3_dataset_explorer.py`. Each renders data fetched exclusively via `api_client`.
29. **Manual smoke test** — run `uvicorn research.api.main:create_app` (as module) on 8000 + `streamlit run research/dashboard/app.py` on 8501; navigate all 3 pages with seeded data; confirm data renders and CORS works (no browser console CORS errors).
   - **Gate F:** dashboard renders all 3 pages against live API.
**Commit** `feat(research/dashboard): streamlit dashboard on research API (API-only data path)`.

### Phase G — Final Gate + DoD E2E
30. **Full gate sweep** — `make lint typecheck import-check test test-replay` (expect unit+integration > 197, replay ≥7).
31. **Import-linter re-verify** — `research/` imports only `analytics`,`intelligence`,`domain`,`persistence`,`transport`,`platform`; `dashboard/api_client` imports `httpx` only — no `persistence`, no `analytics`, no `domain`, no `acquisition`/`processing`. `make import-check` 8/8 + a targeted grep confirming dashboard only imports `httpx`.
32. **DOC-002 E2E test** — answer "why did this token gain momentum" purely via API: `GET /pairs` → find pair; `GET /pairs/{id}/bars` (1m) → compute momentum via price change; `GET /entities/{id}/features/liquidity_growth_pct_1h?as_of=...` → confirm liquidity inflow; assemble textual answer in a Jupyter/pytest (not a DB client). Assert the pipeline is pure HTTP.
33. **`docs/implementation/ImplementationPlan.md`** — commit P0 doc fix + M9 DoD ✅ (append the "✅ Verified" line matching M4-M8 convention).
34. **Final commit + push** to `origin/master`.

---

## 3. Risk Register

| Risk | Likelihood | Severity | Mitigation |
|------|-----------|----------|-----------|
| Cursor pagination unstable / cursor key collision when appending a row mid-pagination | Medium | High | Keyset on `fact_id`/`snapshot_timestamp` (append-only order), `(start, end)` bounds, verify page-to-page no gap/dup under concurrent `save_*` in the integration test. |
| PIT returns wrong (look-ahead) Feature | Medium | High | Dedicated Phase D PIT suite: create row after `as_of`, assert excluded; same `as_of` returns identical row; DOC-014 `(entity_id, feature_name, as_of DESC)` index honored. |
| Dataset endpoint slow on 90-day multi-table join | Medium | Medium | `asyncio.gather` on the three independent hypertable reads; enforce 90-day cap (422); N+1 avoided — each is one index-seek query. |
| Dashboard bypasses API (queries DB directly) | Low | High | api_client.py is the only HTTPX entry; code review + targeted import grep in the test stage (`dashboard` imports `httpx`, `streamlit`, `api_client` only); the grep is added to a CI stage. |
| Import-linter violation (research imports processing/acquisition) | Low | High | Routes import only `persistence.*` repositories and `domain.*`; `make import-check` 8/8 at every wire-stage. |
| CORS blocks Streamlit | Low | Low | `allow_origins=["http://localhost:8501"]` verified in Phase B + manual smoke; expand to other localhost ports only if Dashboard port differs. |
| OpenAPI not agent-readable | Medium | Medium | Every router has `summary` + `description`; response models are real Pydantic; enum params are `Enum`, not free-text `str`. |
| New `data` meta isn't yet backed → dataset `features` empty | Medium | Low | If the pipeline hasn't run long enough, `features`/`bars`/`snapshots`/`outcomes` correctly return `[]`; tests seed fixtures so the shape is exercised, not the empty-path only. |
| `as_of` parse errors returning 500 instead of 422 | Low | Medium | Pydantic `Query(..., as_of: datetime)` gives 422 on bad format; unit test passes a malformed `as_of` and asserts 422. |
| Dashboard `asyncio.run` misuse in Streamlit event loop | Low | Medium | Wrap each api_client method in its own `asyncio.run`; Streamlit runs sync context; no persistent loop. |

---

## 4. Definition of Done Verification Matrix

| DoD Item | Verification Method | Automated? |
|----------|--------------------|------------|
| Human/agent can ask the platform a question | E2E Phase G-32: answer "why did this token gain momentum" via API calls only (Python script / pytest `test_agent_question.py`), assert it imports only `research.api` (never `persistence` directly) and that the answer is derived from API responses | Partial (scripted; final comprehension is a human acceptance) |
| All DOC-015 endpoints implemented | Integration parameterized test hits every route, asserts 200/**404** as documented | Yes |
| Cursor pagination works | Integration loops `next_cursor` through full collection; asserts no dup/no gap, terminating | Yes |
| PIT correctness enforced | Phase D suite: same `as_of` reproducible; row after excluded | Yes |
| Dashboard uses API only | Manual dashboard smoke + grep in `dashboard/` (only `httpx`/`streamlit`/`api_client` imported) | Yes (grep is automatable in CI) |
| OpenAPI at `/v1/openapi.json` | Integration `GET /v1/openapi.json` = 200 + `paths` non-empty | Yes |
| All gates green | `make lint/typecheck/import-check/test/test-replay` | Yes |
| Read-only guarantee | HTTP method test: for every route the method is GET, and `OPTIONS` is not an accidental write; WARN if `allow_methods` other than GET | Yes |

---

## 5. Out-of-Scope Confirmation (explicitly NOT in Milestone 9)

- [ ] Authentication / authorization (deferred — DOC-015 pillar 2 trigger = multi-user Phase 2/3)
- [ ] Rate limiting (deferred — single local user)
- [ ] WebSocket / push / real-time endpoints (DOC-005 Phase 7+)
- [ ] Any POST/PUT/PATCH/DELETE resource — API is strictly `GET`
- [ ] Wallet connection / wallet auth (MVP Non-Goal, DOC-003)
- [ ] Trade execution / portfolio — Strategy ranks, doesn't act, and the API never acts on its behalf (DOC-009)
- [ ] Advanced dashboard features: multi-user, live charts, user persistence
- [ ] Bulk data export endpoint (deferred Phase 2+ migration trigger)
- [ ] Persisting provider responses / new derivatives; this milestone adds NO new persisted Canonical Schema.

---

## 6. Questions / Blockers

**Q1 (BLOCKING — needs decision):** `blockchain_facts` has **no `pair_id` column**; `/v1/pairs/{id}/facts` must be served by a JSONB `payload->>'pool_address'`/`->>'pair_address'` filter. This is a full (non-indexed) JSONB scan on a potentially large table. Options: (a) add a functional/GIN index on `payload->>'pool_address'` (new migration, touching M1/M3 `blockchain_facts`), (b) build without an index (fine for MVP single-chain data volume, block_count token in the hundreds), or (c) add a persisted `pair_id` column to `blockchain_facts` (historical backfill required — scope creep). **Recommendation: (b)** — MVP data is tiny (fits a laptop); add the GIN functional index later only if the profile demands it. Confirm.

**Q2 (BLOCKING):** The uncommitted M8 `ImplementationPlan.md` change (P1) — OK to commit it at the start of Phase G along with the M9 DoD update? (This keeps a single clean M9 commit and doesn't orphan a stray doc diff.) Recommended: yes.

**Q3 (BLOCKING):** `/v1/health` — should it (a) return liveness only (no DB check) or (b) probe Postgres/Redis (`SELECT 1`) and report degraded? Recommendation: (a) liveness-only + version — a health endpoint that fails when the DB is down is anti-useful; a separate `/v1/ready` could cover readiness later. Confirm acceptance.

**Q4 (design):** The dashboard's Streamlit pages are thin and made solely of `api_client` calls. Should the dataset page do (a) a raw "download JSON" action or (b) a Polars-based `.pivot()` demo shown live? Recommendation: (a) now, (b) later — keep M9 minimal; the pivot reshape is a Phase-7 (ML foundation) nicety.

**Q5 (maintainability):** The `list_all_trading_pairs` added in M8 (`entity_repositories`) is superseded by the new `list_pairs` keyset from Phase 0. Keep both (backwards-safe) or replace the one guard call site? Recommendation: keep `list_all_trading_pairs` (used by `outcome_job` — no breakage), add the new paged `list_pairs`, do not remove.

**NEXT:** After sign-off on the D1–D13 decisions + Q1–Q5, **Phase 0** (persistence read layer) begins — it is the true foundation and every later phase waits on it. Do not start Phase A (app factory) until Gate 0 is green; do not start Phase B until Gate A is green.