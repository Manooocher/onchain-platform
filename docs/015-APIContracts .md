---
id: DOC-015
title: API Contracts
version: 1.2
status: Draft
owner: CTO
last_updated: 2026-07-13
tags:
  - api
  - rest
  - contracts
  - implementation-policy
related_docs:
  - DOC-001 Vision
  - DOC-003 MVP
  - DOC-006 Domain Model
  - DOC-008 Canonical Glossary
  - DOC-009 System Capabilities
  - DOC-010 Technology Stack
  - DOC-011 Repository Structure
  - DOC-012 Canonical Schema Specification
  - DOC-013 Coding Standards
  - DOC-014 Persistence Policy
---

# API Contracts

> Every document before this one decided what a piece of data *is*. This one decides what it looks like on the wire, to a client this platform doesn't control and can't assume is a human.

---

# Purpose

**Scope boundary, stated once so it never needs restating:** the technology (FastAPI) is decided in DOC-010 and not reopened here. The exact shape of every field is decided in DOC-012 and not re-derived here. Which query is fast is decided in DOC-014 — this document designs the endpoints that call those queries, it doesn't redesign the indexes behind them. What's left, and what this document owns exclusively, is everything a client sees: URL structure, request parameters, response shape, pagination, error format, and the OpenAPI contract itself.

Three design pillars govern every decision below. None of them is asserted — each is a direct, citable consequence of a decision already made elsewhere in this document set:

1. **Read-only.** DOC-009 gives the Research Platform capability exactly six responsibilities — dataset generation, historical replay, feature inspection, API exposure, visualization, research reproducibility — and none of them is a write. DOC-009's Strategy capability states explicitly that it does *not* execute trades, manage portfolios, or allocate capital. DOC-003 lists automated trading, wallet authentication, and notifications as MVP Non-Goals. There is no write path from an external client to this platform anywhere in the document set — the only writes this system performs come from its own internal pipeline (`acquisition/` → `processing/` → `domain_management/`/`analytics/`). This API exposes `GET` and nothing else.
2. **No authentication in the MVP.** DOC-010 § Security states this directly: *"User authentication, authorization, and network-exposed API hardening are not addressed here and should not be assumed to exist until a dedicated ADR introduces them — expected no earlier than the Research Platform becoming multi-user."* DOC-009's Capability Maturity table places that transition at Phase 2 ("Collaboration") and Phase 3 ("Multi-user Workspace"). Building auth into the MVP API would be solving a problem this project has explicitly deferred.
3. **Agent-readable, not just human-readable.** DOC-001 names the platform "AI-Native" as a first-class architectural property, not an integration bolted on later. DOC-010 gives this as the specific reason FastAPI was chosen over Flask/Django: *"AI-friendly — agents can read the OpenAPI spec directly."* DOC-008 goes further and constrains *how*: *"LLM Context should originate only from Canonical Documents, Canonical Schemas, Canonical Glossary — never directly from raw provider payloads."* That rule is not just about prompts fed to a model; it is a constraint on this API's response bodies. See § Response Shape.

---

# Versioning & URL Structure

Every route is prefixed `/v1/`. There is no unversioned route and no assumption that `/v1/` is temporary — DOC-010's Migration Trigger pattern applies here exactly as it does to every technology choice: `/v1/` is not replaced until a breaking change is unavoidable, at which point `/v2/` is added alongside it, not in place of it, for exactly as long as any consumer still needs `/v1/`.

Resource paths are plural nouns, matching the Domain Model's entities and schemas (DOC-006): `/pairs`, `/tokens`, `/wallets`, `/entities`. There is no verb in a path — an action belongs in the HTTP method or a query parameter, never a path segment.

---

# Resource Model

Every resource below is a thin HTTP surface over a Canonical Schema (DOC-012) or Entity (DOC-012 Part A). This table is the map from "a concept this project already defined" to "a URL a client can call" — nothing in this table introduces a new concept.

| Resource | Source (DOC-012) | Identity in the URL |
|---|---|---|
| `/pairs/{id}` | `TradingPair` (Part A) | Canonical ID |
| `/tokens/{id}` | `Token` (Part A) | Canonical ID |
| `/wallets/{id}` | `Wallet` (Part A) | Canonical ID |
| `/pairs/{id}/bars` | `MarketBar` (§ B.3) | — (collection, filtered by `pair_id`) |
| `/pairs/{id}/facts`, `/facts/{fact_id}` | `BlockchainFact` (§ B.1) | `fact_id` — see § Endpoint Catalog for the URL-encoding note |
| `/entities/{id}/snapshots` | `ObservationSnapshot` (§ B.2) | — (collection, filtered by `entity_id`) |
| `/entities/{id}/features`, `/entities/{id}/features/{name}` | `Feature` (§ B.3) | `entity_id` + `feature_name` |
| `/entities/{id}/insights` | `Insight` (§ B.4) | — (collection, filtered by `entity_id`) |
| `/entities/{id}/outcomes` | `Outcome` (§ B.4) | — (collection, filtered by `entity_id`) |

`SmartContract`, `Metadata`, and `Checkpoint` (§ B.0) have no endpoint. `SmartContract` and `Metadata` are not independently researched objects — they enrich `Token`/`TradingPair` responses (§ Response Shape) rather than standing alone. `Checkpoint` is internal pipeline state, not a research artifact, and exposing it would leak an operational concern (DOC-013 § Dependency & Composition's spirit — infrastructure state does not leak upward — applies to API surfaces too, not only Python imports.

`StateProjection` (§ B.2) also has no endpoint: DOC-012 defines it as Redis-cached, never persisted with its own historical meaning. A client asking "what is the current state" is better served by the *latest* `ObservationSnapshot`, which is durable and has a timestamp — `StateProjection` answers a question this API doesn't need to answer differently from that.

`/entities/{id}` is deliberately generic across `TradingPair`/`Wallet`/`Token` — DOC-012 already made `entity_id` polymorphic this way for `Feature`, `ObservationSnapshot`, `Outcome`, and `Insight` (DOC-014 § Data Integrity Constraints notes this is an application-level responsibility, not a foreign key); the API's URL structure mirrors that decision instead of inventing three parallel sets of routes that would immediately drift from it.

---

# Response Shape & Serialization

**A response body is a Canonical Schema, serialized — never a separate API DTO.** `research/api/routes.py` imports response models directly from `domain/schemas/` and `domain/entities/` (DOC-011 already permits this: `research/` depends on `Market Analytics, Intelligence`, and everything depends on `domain/`). Introducing a parallel, hand-maintained "API response model" that happens to resemble a Canonical Schema is exactly the translation-layer risk DOC-011 already refused elsewhere — *"Business Logic must never see ORM models"* exists to stop one drift-prone duplicate layer; a bespoke DTO layer here would be the same mistake at the API boundary instead of the persistence boundary.

This has a concrete, favorable consequence for Financial Precision: DOC-012 types every monetary/amount field as Python `str` at the schema level specifically (not `Decimal`) — `open`, `close`, `volume_base`, `total_supply`, and every other Token Amount or Price field. Because the Canonical Schema *is* the response model, these fields serialize to JSON as strings automatically, with no custom encoder, no risk of FastAPI's default JSON encoder silently widening a value to a float. DOC-008's requirement — *"Representation: JSON: String"* — is satisfied by construction, not by a rule someone has to remember to apply at the API layer.

**Single-resource `GET`s return the schema body directly** — `GET /v1/pairs/{id}` returns a `TradingPair` object, not `{"data": {...}}`. **Collection endpoints return a pagination envelope:**

```json
{
  "items": [ /* array of the resource's Canonical Schema */ ],
  "pagination": {
    "next_cursor": "eyJmYWN0X2lkIjogIjg0NTM6MHhhYi4uLjo0MiJ9",
    "has_more": true
  }
}
```

**Pagination is cursor-based, not offset-based, and this is not a style preference.** Every collection endpoint in this API queries a table that is being continuously appended to by the ingestion pipeline while a client may be mid-page (`blockchain_facts`, the three TimescaleDB hypertables). Offset pagination (`?page=3`) computes "skip N rows" against a table whose row order can shift between the client's requests — a client can silently skip or duplicate rows. A cursor is an opaque, base64-encoded pointer built from the last row's own ordering key — `fact_id` for Facts, `bar_start_time` for Bars, `as_of_timestamp` for Features. (`pair_id` and `interval` are not part of a Bars cursor: both are already fixed by the URL and query parameters for `/pairs/{id}/bars`, not values that vary row-to-row within a single paginated response.) A cursor built this way is stable regardless of what's written after it was issued. Default page size is `100`; maximum is `1000`, enforced server-side (a `limit` above `1000` is clamped, not rejected — this is a read-only research API, not a billing-metered product, and a hard 4xx here serves no one).

---

# The Point-in-Time Query Pattern

This is the one query shape every other decision in this document exists to support correctly, because DOC-008 § Point-in-Time Correctness is not optional and an API is the easiest place in the whole system to violate it by accident.

`GET /v1/entities/{id}/features/{feature_name}?as_of={timestamp}`

Resolves to exactly the query DOC-014 § Indexing Strategy built the `(entity_id, feature_name, as_of_timestamp DESC)` index for: the most recent `Feature` row with `as_of_timestamp <= as_of`. `as_of` is optional and defaults to the current server time — a dashboard asking "what is this worth right now" and a backtest asking "what did this look like on March 3rd" are the same query with a different literal value, not two different endpoints.

```
GET /v1/entities/eip155:8453/pair:0xAb58.../features/liquidity_growth_pct_1h?as_of=2026-06-01T00:00:00Z

{
  "schema_version": "1.0",
  "feature_id": "liquidity_growth_pct_1h|eip155:8453/pair:0xAb58...|2026-06-01T00:00:00+00:00",
  "feature_name": "liquidity_growth_pct_1h",
  "entity_id": "eip155:8453/pair:0xAb58...",
  "entity_type": "TRADING_PAIR",
  "as_of_timestamp": "2026-06-01T00:00:00+00:00",
  "value": 0.0412
}
```

`feature_id` joins its three components with `|`, not `:` (DOC-012 § Composite ID Delimiter) — `entity_id` and the timestamp each already contain `:` internally, so `|` is what keeps this identifier actually splittable.

`value` is the one field above that is a bare JSON number rather than a string. That is deliberate, not an inconsistency with § Response Shape's rule that monetary fields serialize as strings: DOC-012 types `Feature.value` as native `float` specifically, for Polars vectorization (§ Clarifying an ambiguity in DOC-008) — the one field in the entire schema set where that rule applies. Every other numeric field in this API — `open`/`close`/`volume_base`/`total_supply` and the rest — still serializes as a string, unchanged.

No `Feature` row satisfying the filter is a `404`, not an empty `200` — there is a real difference between "this value is zero" and "this value did not exist yet," and collapsing that distinction is exactly the kind of silent ambiguity DOC-001's "Explainable" principle exists to prevent.

`GET /v1/entities/{id}/features?as_of={timestamp}` is the multi-feature form — every `feature_name` known for that entity, each resolved independently to its own most-recent-as-of-`as_of` row. This is a heavier query (a "latest per group" pattern, not a single index seek) than the single-feature form; it still uses the same index, one seek per distinct `feature_name` rather than one seek total. Documented here so it isn't assumed to cost the same as the version above.

---

# Error Handling

DOC-013's `PlatformError` hierarchy (§ Exception Hierarchy) was designed around the ingestion pipeline — most of it does not apply here, and pretending otherwise would mean forcing internal failure modes through a vocabulary built for a different boundary. The API layer has its own, smaller mapping:

| Situation | HTTP Status | Notes |
|---|---|---|
| Malformed query parameter (bad `interval` enum, unparseable `as_of`) | `422` | FastAPI's own default for a `Query(...)`-annotated parameter that fails Pydantic validation — not a custom choice, and not routed through `DomainValidationError`, which is for business-rule violations *after* parsing succeeds, not parsing itself. |
| Resource or PIT value does not exist | `404` | A normal, expected outcome of a query against real data — not an error condition, and never logged above `DEBUG` (DOC-013 § Observability in Code's severity policy applies to the API layer too). |
| `PersistenceError` or any other `PlatformError` subclass reaches the API layer | `500` | Logged with full context server-side; the response body never includes the internal exception message or a stack trace — only a correlation ID the operator can grep logs for. |
| Everything else unhandled | `500` | Treated as a bug, not a documented contract — if this ever needs its own row, that's a sign a new `PlatformError` subclass is missing in DOC-013, not that this table needs a catch-all. |

Every error response, regardless of status, shares one body shape:

```json
{ "error": { "code": "FEATURE_NOT_FOUND", "message": "...", "correlation_id": "..." } }
```

`code` is a stable, machine-matchable string (screaming snake case) — this is the field an agent parses; `message` is for a human reading a dashboard, and its wording may change between versions without that being a breaking change. `correlation_id` ties a client-visible error to the corresponding server-side log line (DOC-013 § Observability in Code) without exposing anything internal in the response itself.

---

# Endpoint Catalog

Every endpoint is `GET`. `{id}` is always a URL-encoded Canonical ID (DOC-008 format `eip155:<chain_id>/<entity_type>:<address>` — the `/` and `:` characters require percent-encoding as a path segment; this is a client-side encoding detail, not an API design choice, but worth stating so it isn't discovered by trial and error).

| Path | Purpose | Key query parameters |
|---|---|---|
| `/v1/health` | Liveness/readiness (fulfills the endpoint DOC-010 § Observability flagged as "expected alongside the first FastAPI deployment") | — |
| `/v1/pairs` | Discover/list trading pairs | `chain_id`, `dex`, `created_after`, `cursor`, `limit` |
| `/v1/pairs/{id}` | Single `TradingPair`, with its `LiquidityPool` and `Metadata` nested | — |
| `/v1/pairs/{id}/bars` | OHLCV history | `interval` (required, `1m\|5m\|15m\|1h`), `start`, `end`, `include_provisional` (default `false` — see note below), `cursor`, `limit` |
| `/v1/pairs/{id}/facts` | Raw Facts for a pair, for audit/research | `fact_type`, `start`, `end`, `include_unfinalized` (default `false`), `cursor`, `limit` |
| `/v1/pairs/{id}/dataset` | Assembled Research Dataset (DOC-008: Facts → Observations → Bars → Features → Outcomes, joined) | `interval` (required, same enum as `/bars` — see § The Research Dataset Assembly for why this endpoint needs it too), `start`, `end` (**both required** — see note below), `feature_names` (comma-separated) |
| `/v1/facts/{fact_id}` | Single Fact by its natural key | — |
| `/v1/tokens/{id}` | Single `Token`, with `SmartContract` and `Metadata` nested | — |
| `/v1/wallets/{id}` | Single `Wallet` entity | — |
| `/v1/wallets/{id}/activity` | Wallet Activity (DOC-006: *"consists entirely of Blockchain Facts"* — not a separate schema, a Facts query filtered by wallet involvement instead of by pair) | `start`, `end`, `cursor`, `limit` |
| `/v1/entities/{id}/snapshots` | `ObservationSnapshot` history | `start`, `end`, `cursor`, `limit` |
| `/v1/entities/{id}/features/{name}` | Point-in-Time single-feature lookup — § The Point-in-Time Query Pattern | `as_of` (optional, defaults to now) |
| `/v1/entities/{id}/features` | Point-in-Time, all features | `as_of` (optional, defaults to now) |
| `/v1/entities/{id}/insights` | `Insight` history | `start`, `end`, `insight_type`, `cursor`, `limit` |
| `/v1/entities/{id}/outcomes` | `Outcome` (ground truth) history | `outcome_type`, `cursor`, `limit` |

**`include_provisional` defaults to `false` for a specific, non-negotiable reason:** DOC-012 states a provisional `MarketBar` (`is_provisional = true`, built from `CONFIRMED`-not-yet-`FINALIZED` Facts) is *"never for research datasets"* and that filtering it out is `research/`'s explicit query-time responsibility. Defaulting to `false` makes the safe behavior the path of least resistance; a caller building a live, low-latency dashboard opts in explicitly and takes on the responsibility of never feeding that response into anything DOC-008 would call a Research Dataset.

**`/pairs/{id}/dataset` requires a bounded range, on purpose.** DOC-004 and DOC-010 commit this platform to local-first, single-machine operation for the MVP; an unbounded multi-table join across Facts, Snapshots, Bars, Features, and Outcomes for an entity's entire history is exactly the kind of query that turns a laptop into a space heater. `start`/`end` are required (not optional-with-a-default) and the server enforces a maximum span — `90 days` is this document's starting number, matching DOC-014's own "starting point, not a permanent commitment" framing for chunk intervals. A future async, job-based bulk-export endpoint is a Phase 2+ Migration Trigger, not an MVP requirement.

---

# The Research Dataset Assembly

`/pairs/{id}/dataset` is the one endpoint in this catalog assembling more than one Canonical Schema into a single response, and it is the endpoint every other decision in this document set was implicitly building toward: the "X (features) → y (outcome)" shape a researcher or an agent actually needs to start modeling, in one call instead of four.

**What it includes, and what it deliberately excludes:** `bars`, `features`, and `outcomes` — not raw `Facts`, not `ObservationSnapshot`s. DOC-008 describes the full lineage as *"Facts → Observations → Bars → Features → Outcomes,"* and it is tempting to read that as an instruction to embed all five stages here. It isn't one — that phrase describes derivation, not response shape, and `/pairs/{id}/facts` and `/entities/{id}/snapshots` already exist as dedicated endpoints for exactly those two stages. A 90-day window can easily contain tens of thousands of raw Facts; embedding them here would recreate the "space heater" problem the bounded range above exists to prevent, for data a caller building a training set has no direct use for — `bars` and `features` are already the point-in-time-correct, aggregated view derived *from* those Facts. A caller who genuinely wants both calls both endpoints.

**Why `interval` is required here too:** `bars` is time-series at a specific granularity, the same way `/pairs/{id}/bars` is — this endpoint was missing that parameter until now, which made "give me the bars" as ambiguous here as it would be if `/bars` itself had no `interval`.

**Why `features` stays an array of `Feature` objects — vertical, not pivoted into columns:** DOC-012 stores Features as one row per `(feature_name, as_of_timestamp)` pair, not one row per timestamp with a column per feature — Bars are naturally "horizontal" (fixed OHLCV columns), Features are naturally "vertical" (a variable, growing set of names), and forcing them into one shared tabular shape would mean inventing a response format that matches neither Canonical Schema exactly. § Response Shape & Serialization already commits this API to never introducing a bespoke format that isn't a Canonical Schema, serialized — pivoting `features` into a wide table here would be exactly that violation, just for a `GET` response instead of a persisted DTO. A Polars-based caller — human or agent — pivots with one `.pivot()` call; the API's job is fidelity to DOC-012, not doing that reshape on the caller's behalf.

**Why `outcomes` is a plural array, not a single object:** an entity can accumulate more than one `Outcome` over a date range — different `outcome_type`s, evaluated at different `evaluation_timestamp`s (DOC-006). Modeling it as a single nullable object would silently drop every Outcome but one.

```json
{
  "pair": {
    "schema_version": "1.0",
    "canonical_id": "eip155:8453/pair:0xAb58...",
    "chain_id": 8453,
    "dex": "uniswap_v2",
    "base_token_id": "eip155:8453/token:0x4200...",
    "quote_token_id": "eip155:8453/token:0x8335...",
    "pool_address": "0xAb58...",
    "creation_block": 18234110,
    "creation_fact_id": "8453:0xAb58...:0"
  },
  "bars": {
    "interval": "1h",
    "items": [
      {
        "schema_version": "1.0",
        "bar_id": "1h|eip155:8453/pair:0xAb58...|2026-06-01T00:00:00+00:00",
        "pair_id": "eip155:8453/pair:0xAb58...",
        "chain_id": 8453,
        "interval": "1h",
        "bar_start_time": "2026-06-01T00:00:00+00:00",
        "bar_end_time": "2026-06-01T01:00:00+00:00",
        "open": "0.0000412", "high": "0.0000431", "low": "0.0000408", "close": "0.0000419",
        "volume_base": "182340000000000000000000",
        "volume_quote": "7.51",
        "trade_count": 143,
        "vwap": "0.0000417",
        "buy_volume": "96200000000000000000000",
        "sell_volume": "86140000000000000000000",
        "source_fact_range_start": "8453:0xf1a2...:3",
        "source_fact_range_end": "8453:0x9be0...:1",
        "is_provisional": false,
        "computed_at": "2026-06-01T01:00:05+00:00"
      }
    ]
  },
  "features": [
    {
      "schema_version": "1.0",
      "feature_id": "liquidity_growth_pct_1h|eip155:8453/pair:0xAb58...|2026-06-01T00:00:00+00:00",
      "feature_name": "liquidity_growth_pct_1h",
      "entity_id": "eip155:8453/pair:0xAb58...",
      "entity_type": "TRADING_PAIR",
      "as_of_timestamp": "2026-06-01T00:00:00+00:00",
      "value": 0.0412
    }
  ],
  "outcomes": [
    {
      "schema_version": "1.0",
      "outcome_id": "eip155:8453/pair:0xAb58...|SUCCESSFUL_LAUNCH|2026-06-02T00:00:00+00:00",
      "entity_id": "eip155:8453/pair:0xAb58...",
      "outcome_type": "SUCCESSFUL_LAUNCH",
      "observation_window": "24h",
      "label_definition": "Liquidity remained above $10k and no single wallet held >50% of supply for the full window",
      "label_definition_version": "1.0",
      "evaluation_timestamp": "2026-06-02T00:00:00+00:00",
      "evaluated_at": "2026-06-02T00:00:07+00:00",
      "label_value": true
    }
  ]
}
```

`outcomes` is `[]`, not omitted, when no `Outcome` in range has an `evaluation_timestamp` that has closed yet — the same "empty array, not a missing key" rule every other collection-shaped field in this API already follows. No pagination envelope wraps any of the four arrays above — the `90`-day bound enforced on `start`/`end` is this endpoint's size control, the same role a `limit`/`cursor` pair plays on the plain collection endpoints elsewhere in this catalog; adding a second control on top of the first would just be two mechanisms doing one job.

---

# OpenAPI & Agent-Readability Requirements

These are not aspirational — they are the concrete, checkable form of DOC-001's "AI-Native" principle at this specific boundary:

- Every path operation has a `summary` and `description`. A route with no description is incomplete, the same category of incomplete as a Canonical Schema field with no `Field(description=...)` (DOC-012's own convention, extended here).
- Every response model is a real Pydantic model (the Canonical Schema itself, per § Response Shape) — never `dict`, never `Any` (DOC-013 § Determinism Discipline's "No `Any` in Capability Interfaces" applies to `research/api/` exactly as it does to every other Capability).
- Every enum-typed query parameter (`interval`, `fact_type`, `outcome_type`, `insight_type`) is declared as a Python `Enum`, not a free-text `str` with a description saying what's valid — FastAPI renders an `Enum` as a closed `enum` list in the OpenAPI schema, which is the difference between an agent *knowing* the valid values and an agent *guessing* them from prose.
- The generated OpenAPI document is served at `/v1/openapi.json` — not hidden, not behind the interactive docs UI only. A future agent fetching the contract programmatically should not need to scrape HTML to get it.

---

# Security & Cross-Origin Policy

**No authentication, by design, for the MVP** — see § Purpose pillar 2. This is not "authentication we haven't built yet"; it's a documented, deliberate deferral with a stated trigger (DOC-009 Capability Maturity, Research Platform reaching Phase 2/3). The moment that trigger fires, this section — and only this section, not the resource model or the response shapes above it — needs a revision.

**CORS is open for local development, on purpose.** The Streamlit dashboard (DOC-010 § Research Workspace) and the FastAPI backend run as separate local processes on different ports; a permissive CORS policy scoped to `localhost` origins is what lets them talk to each other with zero configuration, consistent with DOC-004's "Local First" principle. This is not the same decision as "CORS is open to the public internet" — it narrows automatically the moment this API is ever deployed somewhere `localhost` doesn't describe, which is itself the same Phase 2/3 trigger as authentication.

**No rate limiting in the MVP** — a single local user cannot meaningfully rate-limit themselves, and adding the machinery for it now would be solving a multi-tenant problem this platform doesn't have yet (DOC-004: *"Optimize only after identifying real bottlenecks."*).

---

# Explicitly Out of Scope

Restated here from DOC-003's Non-Goals specifically because an API is where a Non-Goal quietly turns into an accidental feature if no one is watching for it:

- No `POST`/`PUT`/`PATCH`/`DELETE` on any resource. Domain Entities and Facts are written exclusively by the internal pipeline (`acquisition/` → `processing/` → `domain_management/`); nothing external mutates platform state.
- No wallet connection, wallet authentication, or any endpoint that would need one.
- No trade execution, order placement, or portfolio endpoints — Strategy (DOC-009) ranks; it does not act, and neither does this API on its behalf.
- No webhook/subscription/push endpoints. This is a pull API. Real-time delivery (if ever needed) is a Phase 7+ concern (DOC-005, Autonomous Research) and an entirely different design problem from anything in this document.

---

# Guiding Principle

> A response body is a promise about what a Canonical Schema already promised, transmitted over HTTP without being retranslated along the way.
> An API that a research agent can't read correctly is not AI-native — it's a human interface with an OpenAPI badge on it.