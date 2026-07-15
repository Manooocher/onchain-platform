---
id: DOC-012
title: Canonical Schema Specification
version: 1.5
status: Draft
owner: CTO
last_updated: 2026-07-13
tags:
  - schemas
  - data-contracts
  - pydantic
  - implementation
related_docs:
  - DOC-006 Domain Model
  - DOC-007 Data Flow
  - DOC-008 Canonical Glossary
  - DOC-010 Technology Stack
  - DOC-011 Repository Structure
  - DOC-013 Coding Standards
  - DOC-014 Persistence Policy
---

# Canonical Schema Specification

> DOC-006 and DOC-008 define what a Blockchain Fact *is*. This document defines what one *looks like* — field by field, type by type — so it can be typed directly into `domain/schemas/` without a single open question.

> Part A below is typed into `src/onchain_platform/domain/entities/`; Part B is typed into `src/onchain_platform/domain/schemas/` (both per DOC-011). The split mirrors DOC-006: Part A is the slowly-changing Structural Domain, Part B is the Data Lifecycle's temporal concepts. Nothing in this document is a database table — PostgreSQL/TimescaleDB column mapping is `persistence/`'s job, not this document's.

---

# Purpose

Every module in this platform communicates exclusively through Canonical Schemas (DOC-008). This document is the literal contract: exact field names, exact types, exact JSON shape, one worked example per schema. If a field is not listed here, it does not exist yet — add it here first, then in code.

---

# Conventions (apply to every schema below)

| Rule | Detail |
|---|---|
| `schema_version` | Every schema starts with `schema_version: str`, e.g. `"1.0"`. Bump on any field addition/removal/type change. See § Versioning Policy. |
| Canonical ID | `eip155:<chain_id>/<entity_type>:<checksummed_address>` (DOC-008). Addresses are always EIP-55 checksummed — a schema-level validator, not a convention left to callers. |
| Timestamps | All three of `event_time`, `observed_at`, `ingested_at` (DOC-008 Triple Timestamp Standard) are timezone-aware UTC, serialized as ISO-8601 (`2026-07-11T14:32:05Z`). A naive datetime is a validation error, not a warning. |
| Financial values | Per DOC-008 Financial Precision Principle: Python `Decimal`, JSON `string`, DB `NUMERIC`. Never `float`. This applies to any field representing an actual on-chain quantity or price. It does **not** automatically apply to derived ratios — see the clarification below. |
| Naming | `snake_case` everywhere, matching Pydantic/Python convention. |
| Identifiers | Composite natural keys wherever possible (ADR-006 § Idempotency) — no surrogate UUIDs for Facts. |

## Clarifying an ambiguity in DOC-008: are *all* Feature values Decimal?

DOC-008's Financial Precision Principle bans `float` for "financial values." A raw amount, a price, a volume — unambiguously financial, always `Decimal`. But a derived analytical value like `wallet_concentration` (a Gini-style ratio) or `volatility` (a standard deviation of returns) is not itself an on-chain quantity that must reconcile to a wei-exact balance.

**The test is categorical, not unit-based: was this value computed by the Feature Engine, or is it a direct pass-through of a Snapshot/Fact field?** An earlier version of this section framed the test as "is the value dimensionless" — that's the wrong test. A feature like `liquidity_usd_growth_1h` genuinely carries a USD dimension; calling it "dimensionless" is a category error, not just an imprecise label. What actually makes it a Feature (and therefore `float`) is that the Feature Engine *computed* it — a rate of change over a window, derived from more than one underlying Decimal reading — not that it lacks units.

**If a value is a direct pass-through** of a single `ObservationSnapshot` or `BlockchainFact` field with no real computation — e.g., "just give me `ObservationSnapshot.liquidity_usd` under a Feature name" — **it should not be modeled as a Feature at all.** Join directly to the Snapshot for that value; manufacturing a pass-through Feature only to relax its type to `float` would be reintroducing the Financial Precision violation through the back door.

**This is not merely acceptable — it is necessary.** `Feature.value` is consumed almost exclusively through Polars (DOC-010), which is columnar and vectorized over native `float64` arrays. Storing `Feature.value` as `Decimal`/`str` would force a per-row Python-level parse before any vectorized computation could run at all, defeating the entire reason Polars was chosen over Pandas in the first place. Rounding drift in a momentum score or a USD growth rate has no reconciliation consequence the way a mis-recorded token balance would — so there is no correctness cost to this, and a real performance cost to avoiding it.

**Rule adopted here:** any field that is a direct on-chain amount, price, or unmodified pass-through of one uses `Decimal` and is not a Feature. Any field genuinely computed by the Feature Engine from one or more Decimal inputs is `float` in the Feature schema, regardless of whether the result still carries a monetary unit like USD (see § Feature Naming Convention — the `_usd` suffix communicates units to the reader; it does not change the storage type). The computation itself must still use `Decimal` inputs internally; only the final output value's storage type relaxes. This is called out explicitly in § Feature below and should be treated as a ratified clarification of DOC-008, not a violation of it.

## Composite ID Delimiter: why `|`, not `:`, for four of the five composite keys

Five schemas below build their identifier by concatenating natural-key components: `fact_id`, `snapshot_id`, `bar_id`, `feature_id`, `outcome_id`. All five were originally written using `:` as the join character. One of them is safe that way; four are not.

`fact_id` (`f"{chain_id}:{tx_hash}:{log_index}"`) is safe: `chain_id` is a bare integer, `tx_hash` is `0x`-prefixed hex with no colon in it, `log_index` is a bare integer. Splitting on `:` always yields exactly three parts.

The other four are not safe, because they each embed **two** components that independently contain `:` — a Canonical ID (`eip155:<chain_id>/<entity_type>:<address>`, DOC-008) and an ISO-8601 timestamp (`2026-06-01T00:00:00+00:00` — the `HH:MM:SS` portion is colons too). Joining those with `:` as the outer separator produces a string that looks structured but cannot be reliably split back into its parts — there is no way to tell, from the colons alone, which one is the outer separator. Nothing in this document set currently reverse-parses these IDs, so this has not been an active bug, but the moment something does (a debugging tool, a cache key scheme, a log line), it silently gets the wrong answer rather than an obvious error.

**Rule:** `snapshot_id`, `bar_id`, `feature_id`, and `outcome_id` use `|` as the outer join character, not `:`. `|` appears in none of their components — not in a Canonical ID, not in an ISO timestamp, not in any enum or name used inside these keys — so `composite_id.split("|")` is always safe and always yields the exact component count. `fact_id` is unchanged; it never had this problem.

---

# Part A — Entity Schemas (Operational Data → PostgreSQL)

These are the slowly-changing registry objects from DOC-006 § Structural Domain. Each has a stable Canonical ID and is owned per DOC-006's Ownership table.

## Blockchain

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `chain_id` | `int` | EIP-155 chain ID (`1`, `8453`, `56`) |
| `name` | `str` | `"Ethereum"`, `"Base"`, `"BNB Chain"` |
| `native_asset_symbol` | `str` | `"ETH"`, `"ETH"`, `"BNB"` |
| `is_supported` | `bool` | `true` only for the three EVM-first chains (DOC-003) |
| `avg_block_time_seconds` | `float` | Used to size the reorg header buffer (ADR-006), not a financial value |

```json
{
  "schema_version": "1.0",
  "chain_id": 8453,
  "name": "Base",
  "native_asset_symbol": "ETH",
  "is_supported": true,
  "avg_block_time_seconds": 2.0
}
```

---

## SmartContract

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `canonical_id` | `str` | `eip155:<chain_id>/contract:<address>` |
| `chain_id` | `int` | |
| `address` | `str` | EIP-55 checksummed |
| `contract_type` | `enum` | `ERC20 \| FACTORY \| ROUTER \| POOL \| UNKNOWN` |
| `is_verified` | `bool` | Source verification status (Metadata Service, DOC-006) |
| `deployment_block` | `int \| None` | `None` if unknown at discovery time |

---

## Token

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `canonical_id` | `str` | `eip155:<chain_id>/token:<address>` |
| `chain_id` | `int` | |
| `contract_address` | `str` | EIP-55 checksummed |
| `symbol` | `str` | |
| `name` | `str` | |
| `decimals` | `int` | |
| `total_supply` | `str` | **Token Amount** (DOC-008) — raw smallest-denomination integer as a string, decimals never pre-applied |
| `deployment_block` | `int` | |

```json
{
  "schema_version": "1.0",
  "canonical_id": "eip155:8453/token:0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
  "chain_id": 8453,
  "contract_address": "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
  "symbol": "TOKEN",
  "name": "Example Token",
  "decimals": 18,
  "total_supply": "1000000000000000000000000",
  "deployment_block": 18234567
}
```

---

## TradingPair

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `canonical_id` | `str` | `eip155:<chain_id>/pair:<pool_address>` |
| `chain_id` | `int` | |
| `dex` | `str` | e.g. `"uniswap_v2"`, `"aerodrome"` |
| `base_token_id` | `str` | Canonical ID of Token |
| `quote_token_id` | `str` | Canonical ID of Token |
| `pool_address` | `str` | EIP-55 checksummed |
| `creation_block` | `int` | |
| `creation_fact_id` | `str` | The `fact_id` of the `PAIR_CREATED` fact that created this pair — traceability back to source |

---

## LiquidityPool

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `canonical_id` | `str` | Same as parent `TradingPair.canonical_id` — a Liquidity Pool does not have an identity independent of its pair in the MVP |
| `protocol` | `str` | |
| `fee_tier_bps` | `int \| None` | Basis points; `None` for fee-less V2-style pools |

Current reserves are **not** stored here — that is live State Projection (§ Part B). This schema only holds the pool's static configuration.

---

## Wallet

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `canonical_id` | `str` | `eip155:<chain_id>/wallet:<address>` |
| `chain_id` | `int` | |
| `address` | `str` | EIP-55 checksummed |
| `first_seen_at` | `datetime` | `event_time` of the first Fact referencing this wallet |
| `tags` | `list[str]` | Empty in MVP. Placeholder for DOC-006 Future Extensions (`developer`, `smart_money`, etc.) — populated by later phases, not MVP logic |

---

## Metadata

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `entity_id` | `str` | Canonical ID of the entity this enriches |
| `website` | `str \| None` | |
| `social_links` | `dict[str, str]` | e.g. `{"twitter": "...", "telegram": "..."}` |
| `logo_url` | `str \| None` | |
| `description` | `str \| None` | |
| `verification_status` | `enum` | `UNVERIFIED \| PENDING \| VERIFIED` |
| `last_updated` | `datetime` | `ingested_at`-equivalent for this metadata record |

Per DOC-006: Metadata never modifies a Blockchain Fact. This schema has no `event_time` — metadata is not a historical occurrence.

---

# Part B — Temporal Schemas

These are the schemas ADR-006 and DOC-006's Data Lifecycle are built around. Correctness here is the platform's core promise.

Unlike Part A, this group does not share one storage location — that was the source of a real ambiguity in earlier drafts of this document. Six distinct storage fates are grouped here only because all six are temporal/derived rather than slowly-changing registry data:

- **B.0 — Operational Metadata (PostgreSQL, mutable).** `Checkpoint` only. Not historical at all — the opposite of B.1's immutability.
- **B.1 — Append-Only (PostgreSQL, MVP).** `BlockchainFact` only. Genuinely immutable once Finalized (DOC-006); resides in PostgreSQL for the MVP per DOC-007, with dedicated append-only storage a possible future migration.
- **B.2 — Derived State (Redis Cache, Not Persisted).** `StateProjection` only. Never written to a historical table at all — always rebuildable from Facts.
- **B.3 — Analytical (TimescaleDB).** `ObservationSnapshot`, `MarketBar`, `Feature`.
- **B.4 — Ground Truth & Research Artifacts (PostgreSQL).** `Outcome`, `Insight`.
- **B.5 — Domain Events (Redis Streams, transient).** `ChainReorgEvent` only. Not state, not a cache, not ground truth — a message consumed once by subscribers, structurally closer to a Fact-adjacent notification than to anything else in this document.

---

## B.0 — Operational Metadata (PostgreSQL, mutable)

### Checkpoint

Tracks ingestion progress per chain, so a restart knows where to resume (ADR-006 § Checkpointing). Read by `acquisition/checkpoint.py`; written/advanced only by `processing/finality_engine.py` (DOC-011) — nothing is finalized, so nothing should advance this, outside the finality engine.

**Mutable, singleton per chain, not append-only.** This is the direct opposite of B.1: there is exactly one `Checkpoint` row per `chain_id`, and it is overwritten in place as ingestion progresses. It has never been append-only, and grouping it anywhere near B.1 without saying so is exactly the kind of ambiguity this Part B intro exists to prevent.

| Field | Type | Notes |
|---|---|---|
| `chain_id` | `int` | Primary key |
| `last_finalized_block` | `int` | The last block number known to be Finalized (DOC-006 Confirmation Lifecycle) |
| `last_finalized_at` | `datetime` | When that block was finalized |
| `updated_at` | `datetime` | When this row was last written |

---

## B.1 — Append-Only (PostgreSQL, MVP)

### BlockchainFact

The canonical, versioned envelope for every finalized (or finalizing) piece of blockchain history.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `fact_id` | `str` | Deterministic natural key: `f"{chain_id}:{tx_hash}:{log_index}"`. No surrogate UUID (ADR-006 § Idempotency). |
| `chain_id` | `int` | |
| `fact_type` | `enum` | `PAIR_CREATED \| SWAP_EXECUTED \| LIQUIDITY_ADDED \| LIQUIDITY_REMOVED` |
| `block_number` | `int` | |
| `block_hash` | `str` | |
| `tx_hash` | `str` | |
| `log_index` | `int` | |
| `event_time` | `datetime` | Block timestamp — actual chain time |
| `observed_at` | `datetime` | When the RPC/provider emitted this to us |
| `ingested_at` | `datetime` | When our platform received it |
| `confirmation_status` | `enum` | `PENDING \| CONFIRMED \| FINALIZED \| ORPHANED` |
| `confirmations` | `int` | Recomputed as the chain advances; monotonic until `FINALIZED` or reset to `0`/removed on `ORPHANED` |
| `payload` | discriminated union | Shape depends on `fact_type` — see below |

**Naming note:** DOC-006's examples list both `Mint`/`Burn` and `LiquidityAdded`/`LiquidityRemoved`. These are not four distinct fact types — `Mint`/`Burn` are the *raw* Uniswap-V2-style event names; `LIQUIDITY_ADDED`/`LIQUIDITY_REMOVED` are the *canonical* `fact_type` values they normalize into. Only the canonical four values above should ever appear in a persisted `BlockchainFact.fact_type`.

**Known future extension — Uniswap V3-style pools:** V3's `Mint`/`Burn` are tick-based (concentrated liquidity within a price range) and are not a proportional add/remove the way V2's are. They are explicitly **not** modeled by the four `fact_type` values above yet. Do not attempt to normalize a V3 Mint/Burn into `LIQUIDITY_ADDED`/`LIQUIDITY_REMOVED` — it will silently misrepresent the position. V3 support requires its own `fact_type` values and payload shape, deferred until V3 pools enter scope.

#### Payload by `fact_type`

**`PAIR_CREATED`**

| Field | Type |
|---|---|
| `pair_address` | `str` |
| `token0_address` | `str` |
| `token1_address` | `str` |
| `dex` | `str` |

**`SWAP_EXECUTED`**

| Field | Type |
|---|---|
| `pool_address` | `str` |
| `sender` | `str` |
| `recipient` | `str` |
| `amount0_in` | `str` (Token Amount) |
| `amount1_in` | `str` |
| `amount0_out` | `str` |
| `amount1_out` | `str` |

**`LIQUIDITY_ADDED`** / **`LIQUIDITY_REMOVED`**

| Field | Type |
|---|---|
| `pool_address` | `str` |
| `provider` | `str` |
| `amount0` | `str` |
| `amount1` | `str` |
| `liquidity_delta` | `str` |

`liquidity_delta` is always a positive magnitude. Direction comes exclusively from `fact_type` (`LIQUIDITY_ADDED` vs `LIQUIDITY_REMOVED`), never from the sign of this field. A negative `liquidity_delta` is a validation error, not a way of encoding removal.

The same rule applies to `amount0` and `amount1` in this payload: both are always positive magnitudes, with direction coming from `fact_type`, exactly as for `liquidity_delta`. This differs from `SWAP_EXECUTED` above, where direction is already unambiguous from the `_in`/`_out` field names themselves.

```json
{
  "schema_version": "1.0",
  "fact_id": "8453:0x9f2c...e21a:14",
  "chain_id": 8453,
  "fact_type": "SWAP_EXECUTED",
  "block_number": 18234599,
  "block_hash": "0x71ab...",
  "tx_hash": "0x9f2c...e21a",
  "log_index": 14,
  "event_time": "2026-07-11T14:32:05Z",
  "observed_at": "2026-07-11T14:32:06Z",
  "ingested_at": "2026-07-11T14:32:06Z",
  "confirmation_status": "CONFIRMED",
  "confirmations": 6,
  "payload": {
    "pool_address": "0x88e6...",
    "sender": "0x1234...",
    "recipient": "0x1234...",
    "amount0_in": "0",
    "amount1_in": "500000000000000000",
    "amount0_out": "1230000000000000000000",
    "amount1_out": "0"
  }
}
```

#### Modeling the discriminated payload (Pydantic pattern)

```python
from typing import Literal, Union, Annotated
from pydantic import BaseModel, Field

class PairCreatedPayload(BaseModel):
    fact_type: Literal["PAIR_CREATED"]
    pair_address: str
    token0_address: str
    token1_address: str
    dex: str

class SwapExecutedPayload(BaseModel):
    fact_type: Literal["SWAP_EXECUTED"]
    pool_address: str
    sender: str
    recipient: str
    amount0_in: str
    amount1_in: str
    amount0_out: str
    amount1_out: str

# LiquidityAddedPayload, LiquidityRemovedPayload follow the same shape:
# their own fact_type: Literal["LIQUIDITY_ADDED"] / Literal["LIQUIDITY_REMOVED"],
# plus the fields listed under § Payload by fact_type above. Omitted here for brevity.

class BlockchainFact(BaseModel):
    schema_version: str = "1.0"
    fact_id: str
    chain_id: int
    fact_type: Literal["PAIR_CREATED", "SWAP_EXECUTED", "LIQUIDITY_ADDED", "LIQUIDITY_REMOVED"]
    block_number: int
    block_hash: str
    tx_hash: str
    log_index: int
    event_time: datetime  # tz-aware, validated
    observed_at: datetime
    ingested_at: datetime
    confirmation_status: Literal["PENDING", "CONFIRMED", "FINALIZED", "ORPHANED"]
    confirmations: int
    payload: Annotated[
        Union[PairCreatedPayload, SwapExecutedPayload, LiquidityAddedPayload, LiquidityRemovedPayload],
        Field(discriminator="fact_type"),
    ]
```

Each payload class carries its own `fact_type` literal — this is not optional decoration, it is the only way Pydantic can actually dispatch the union automatically. `Field(discriminator="fact_type")` tells Pydantic to read `payload.fact_type` and pick the matching class without trying every member in order. Without a discriminator field on each payload, `Union[...]` falls back to trying each type until one parses without error, which is slower and — worse — silently ambiguous whenever two payload shapes could both validate against the same input.

`BlockchainFact.fact_type` and `BlockchainFact.payload.fact_type` are intentionally the same value in two places: the discriminator must live on the union member for Pydantic to read it, and `BlockchainFact.fact_type` stays as its own field because every other document (DOC-006, DOC-007, DOC-009) already treats it as a fact-level attribute, not something nested inside `payload`. A validator enforcing the two stay in sync is an implementation detail for `processing/fact_processor.py`, not a schema concern.

**`processing/schema_dispatcher.py` (DOC-011) does not perform this dispatch.** It routes `schema_version` — deciding which *version* of a model to parse against (V1 vs V2) as schemas evolve. Discriminating between `fact_type` payload shapes is a completely different axis, handled entirely by Pydantic's `discriminator=` mechanism shown above. Conflating the two was the error in an earlier version of this section.

---

## B.2 — Derived State (Redis Cache, Not Persisted)

### StateProjection

The live, mutable, continuously-recomputed read model. **Never persisted as its own historical table** — it is served from Redis cache (DOC-010) and can always be rebuilt by replaying Facts.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `entity_id` | `str` | Canonical ID of the Liquidity Pool this describes |
| `chain_id` | `int` | |
| `as_of_block` | `int` | State reflects the chain up to (and including) this block |
| `as_of_fact_id` | `str` | The last `BlockchainFact.fact_id` that updated this projection |
| `computed_at` | `datetime` | When the Projection Engine last recomputed this — **not** an `event_time`, this schema has no historical meaning of its own |
| `reserve0` | `str` | Token Amount |
| `reserve1` | `str` | Token Amount |
| `price` | `str` | token1 per token0, Decimal-as-string |

---

## B.3 — Analytical (TimescaleDB)

### ObservationSnapshot

The historically-preserved recording of State — this is what makes State auditable after the fact.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `snapshot_id` | `str` | `f"{entity_id}\|{snapshot_timestamp.isoformat()}\|{source}"` — `\|`, not `:` (§ Composite ID Delimiter); `source` is part of the key so two sources snapshotting the same entity at the same instant never collide |
| `entity_id` | `str` | |
| `chain_id` | `int` | |
| `snapshot_timestamp` | `datetime` | The `event_time`-equivalent — the moment this state is asserted to describe |
| `observed_at` | `datetime` | |
| `ingested_at` | `datetime` | |
| `source` | `str` | e.g. `"projection_engine:poll:60s"` |
| `snapshot_version` | `int` | Increments if the *shape* of what's captured for this entity type changes, independent of `schema_version` |
| `reserve0` | `str` | |
| `reserve1` | `str` | |
| `price` | `str` | |
| `liquidity_usd` | `str \| None` | Requires a price oracle; `None` if unavailable |
| `holder_count` | `int \| None` | |
| `market_cap_usd` | `str \| None` | |
| `fdv_usd` | `str \| None` | Fully diluted valuation |

---

### MarketBar

Derived **exclusively** from finalized `SWAP_EXECUTED` facts (DOC-006 — never from `ObservationSnapshot`).

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `bar_id` | `str` | `f"{pair_id}\|{interval}\|{bar_start_time.isoformat()}"` — `\|`, not `:` (§ Composite ID Delimiter) |
| `pair_id` | `str` | Canonical ID of the `TradingPair` |
| `chain_id` | `int` | |
| `interval` | `enum` | `1m \| 5m \| 15m \| 1h` |
| `bar_start_time` | `datetime` | Bucket start, `event_time`-based |
| `bar_end_time` | `datetime` | |
| `open` | `str` | |
| `high` | `str` | |
| `low` | `str` | |
| `close` | `str` | |
| `volume_base` | `str` | |
| `volume_quote` | `str` | |
| `trade_count` | `int` | |
| `vwap` | `str` | |
| `buy_volume` | `str` | |
| `sell_volume` | `str` | |
| `source_fact_range` | `tuple[str, str]` | `(first_fact_id, last_fact_id)` — every fact between these, inclusive, composed this bar. This is the field that makes a Market Bar reproducible and auditable, not just plausible. |
| `is_provisional` | `bool` | `true` if built from `CONFIRMED`-but-not-yet-`FINALIZED` facts (allowed only for low-latency dashboard use per DOC-007, never for research datasets) |
| `computed_at` | `datetime` | |

**Reconstruction predicate:** a bar's contents are exactly the set of `SWAP_EXECUTED` facts for `pair_id` where `bar_start_time <= event_time < bar_end_time`, restricted to `FINALIZED` facts (or `CONFIRMED`, only when `is_provisional=true`). This predicate, not the `source_fact_range` bounds alone, is the authoritative definition — `source_fact_range` records what the predicate actually matched, for audit.

**On reorg:** if any fact inside an already-computed bar's `source_fact_range` transitions to `ORPHANED`, the entire bar is recomputed from the predicate above — never patched incrementally. Partial correction risks a bar that reconciles to neither the old nor the new canonical history. This is the same trade-off ADR-006 already accepts elsewhere: higher recomputation cost in exchange for a deterministic guarantee.

**Filtering `is_provisional`:** excluding provisional bars from a research dataset is `research/`'s query-time responsibility (`WHERE is_provisional = false`), not a schema-level constraint. The schema permits storing provisional bars because the low-latency dashboard use case in DOC-007 requires it; nothing here should be read as forbidding their existence in the table.

---

### Feature

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `feature_id` | `str` | `f"{feature_name}|{entity_id}|{as_of_timestamp.isoformat()}"` — `\|`, not `:` (§ Composite ID Delimiter) |
| `feature_name` | `str` | e.g. `"liquidity_growth_1h"`, `"buy_pressure_5m"` |
| `entity_id` | `str` | |
| `entity_type` | `enum` | `TRADING_PAIR \| WALLET \| TOKEN` |
| `as_of_timestamp` | `datetime` | **The point-in-time this value is valid for.** This is the field every Point-in-Time-correctness query filters on (see the forthcoming PIT Implementation ADR). |
| `computed_at` | `datetime` | When it was actually computed — may be later than `as_of_timestamp` for backfilled features, but must never be used for PIT filtering |
| `window` | `str \| None` | Lookback window, e.g. `"1h"` |
| `value` | `float` | See § Conventions clarification — derived ratio/statistic, not a raw amount |
| `inputs` | `list[str]` | IDs of the Facts / Observation Snapshots / Market Bars this value was derived from — traceability |

#### Feature Naming Convention

`feature_name` carries its unit as a suffix, purely for the reader — it does not change `value`'s storage type, which is `float` for every Feature regardless of suffix (see § Conventions clarification above).

| Suffix | Meaning | Example |
|---|---|---|
| `_pct`, `_ratio` | Dimensionless proportion, typically 0–1 or a percentage | `buy_pressure_ratio_5m` |
| `_score`, `_zscore` | Dimensionless composite or statistical score | `momentum_zscore_1h` |
| `_usd` | A derived quantity expressed in USD — still `float`; it is an analytical output, not a stored balance | `liquidity_usd_growth_1h` |
| `_delta` | A derived change over a window, in the base unit implied by the rest of the name | `holder_count_delta_1h` |

A feature without one of these suffixes is missing one — add it before merging, rather than leaving `feature_name` ambiguous about units.

---

## B.4 — Ground Truth & Research Artifacts (PostgreSQL)

Neither DOC-007 nor the previous version of this document explicitly assigned `Outcome` or `Insight` a storage location. `Outcome` is assigned here per DOC-007, which already names PostgreSQL for it. `Insight` is a new explicit assignment: it enriches research the same way `Metadata` enriches entities, and has no time-series shape that would justify TimescaleDB.

### Outcome

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `outcome_id` | `str` | `f"{entity_id}\|{outcome_type}\|{evaluation_timestamp.isoformat()}"` — `\|`, not `:` (§ Composite ID Delimiter) |
| `entity_id` | `str` | |
| `outcome_type` | `enum` | `RUG_PULL \| SUCCESSFUL_LAUNCH \| DEAD_TOKEN` |
| `observation_window` | `str` | e.g. `"24h"` |
| `label_definition` | `str` | Human-readable rule description |
| `label_definition_version` | `str` | Rules evolve — this must be versioned independently of `schema_version` so a historical Outcome remains explainable years later |
| `evaluation_timestamp` | `datetime` | When the observation window closed |
| `evaluated_at` | `datetime` | When the Outcome Engine actually ran this evaluation |
| `label_value` | `bool` | Did this outcome occur, per this rule version |

No confidence field — per DOC-008, Outcomes never contain confidence. That belongs to the (currently out-of-scope) `Prediction` schema.

---

### Insight

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `insight_id` | `str` | |
| `entity_id` | `str` | |
| `insight_type` | `str` | e.g. `"SuspiciousLiquidityGrowth"` |
| `summary` | `str` | Human-readable, one to two sentences |
| `generated_at` | `datetime` | |
| `source_features` | `list[str]` | `feature_id`s this Insight summarizes — DOC-008: "Insights summarize Features" |
| `importance` | `enum` | `LOW \| MEDIUM \| HIGH` — a qualitative editorial signal, explicitly **not** an ML confidence score |

Per DOC-008: an Insight never becomes input to a downstream pipeline. No other schema in this document may reference an `insight_id` in an `inputs` field.

---

## B.5 — Domain Events (Redis Streams, transient)

### ChainReorgEvent

DOC-013 § Exception Hierarchy already commits to this: *"Reorgs are modeled as Domain Events (e.g., `ChainReorgEvent`) published to Redis Streams, never as Exceptions."* That sentence has been true without a schema to back it since DOC-013 was written — `processing/finality_engine.py` cannot publish a typed event it has no type for, and a consumer reading `transport/event_stream.py`'s reorg stream has nothing to validate against. This section is that schema.

Not B.0–B.4: it isn't operational metadata, isn't append-only history, isn't cached state, isn't an analytical time series, and isn't a ground-truth artifact. It is consumed once, by whichever subscribers care (a dashboard, a logger, a future alerting path), and then it is gone — Redis Streams retention, not a table, is its only "storage."

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | |
| `event_id` | `str` | `f"{chain_id}\|{fork_block_number}\|{detected_at.isoformat()}"` — `\|`, not `:` (§ Composite ID Delimiter), for the same reason every other composite key in this document uses it |
| `chain_id` | `int` | |
| `fork_block_number` | `int` | The last block number still shared by the old and new canonical chains — the point of divergence, not the point of detection |
| `orphaned_block_range` | `tuple[int, int]` | `(first_orphaned_block, last_orphaned_block)` — deliberately a range, not an embedded list of `fact_id`s. A deep reorg can orphan a large number of Facts; the range lets a consumer query `blockchain_facts WHERE chain_id = :chain_id AND block_number BETWEEN :first AND :last` itself rather than the event carrying a payload sized to the reorg depth. Consistent with `MarketBar.source_fact_range` (DOC-012 § B.3) and with ADR-006's own principle that Redis accelerates the pipeline but the blockchain — recovered through Postgres, here — remains the source of truth. |
| `new_canonical_head_hash` | `str` | The block hash the chain has converged on *after* resolution — `VARCHAR(66)` per DOC-014 § Type Mapping Rules, same as every other block hash in this document set |
| `depth` | `int` | `last_orphaned_block - fork_block_number` — restated as its own field rather than left for a consumer to compute, because it is the number DOC-013's severity policy (§ Observability in Code) keys its log level off of: routine at shallow depth, worth a closer look as depth approaches the configured confirmation depth (ADR-006) |
| `detected_at` | `datetime` | When the Canonical Chain Validation Engine (ADR-006) detected the break in continuity — not an on-chain timestamp, so this is the only timestamp this schema needs; there is no `event_time`/`observed_at` distinction to make for something the platform itself generated |

No `Prediction`-style confidence field, and no `is_provisional` — a `ChainReorgEvent` is only ever published after the engine has already committed to marking the affected range `ORPHANED` (DOC-006 Confirmation Lifecycle); it reports a decision already made, not a probability.

This is the first schema in a category that may grow — future Domain Events (a Checkpoint stall, a provider failover) belong here too, under B.5, rather than each inventing its own storage-fate discussion from scratch.

---

# Schema Versioning Policy

| Change type | Action |
|---|---|
| Add an optional field | Non-breaking. No version bump required, but note it in a changelog comment. |
| Add a required field, remove a field, change a field's type | Breaking. Bump `schema_version` (e.g. `"1.0"` → `"1.1"` for additive-but-required, `"2.0"` for structural). |
| Any breaking change | Old persisted records keep their original `schema_version` forever — they are never rewritten in place (DOC-008: "Schemas must never change in-place"). `processing/schema_dispatcher.py` must retain a parser for every `schema_version` still present in storage. |
| Deprecating a schema version | Only once no query path or replay fixture (DOC-011 `tests/replay/fixtures/`) still exercises it. |

---

# Traceability Chain

Every schema above that is derived (`StateProjection`, `ObservationSnapshot`, `MarketBar`, `Feature`, `Outcome`, `Insight`) carries an explicit pointer back to what produced it (`as_of_fact_id`, `source_fact_range`, `inputs`, `source_features`). This is not decoration — it is the literal, queryable answer to "why does this number say what it says," which is the concrete implementation of DOC-007's Traceability requirement and DOC-001's "Explainable" design principle. A Feature or Insight with an empty `inputs`/`source_features` list should be treated as a bug, not an edge case.