---
id: DOC-014
title: Persistence Policy
version: 1.1
status: Draft
owner: CTO
last_updated: 2026-07-13
tags:
  - persistence
  - engineering
  - database
  - implementation-policy
related_docs:
  - DOC-006 Domain Model
  - DOC-007 Data Flow
  - DOC-008 Canonical Glossary
  - DOC-009 System Capabilities
  - DOC-010 Technology Stack
  - DOC-011 Repository Structure
  - DOC-012 Canonical Schema Specification
  - DOC-013 Coding Standards
  - ADR-006 Blockchain Data Acquisition Strategy
---

# Persistence Policy

> DOC-012 fixed what a field looks like. DOC-012 also says, explicitly, that mapping it onto a database column is not its job. This document is that job — nothing above it gets re-decided here, and nothing below it (an actual migration) should ever need to guess.

---

# Purpose

**Scope boundary, stated once so it never needs restating:** if a rule is about *which field exists and what Python type it has*, it belongs in DOC-012 — this document never re-derives a field's shape. If it's about *which package may touch which file*, it belongs in DOC-011. If it's about *which database technology was chosen and why*, it belongs in DOC-010 — PostgreSQL, TimescaleDB, SQLAlchemy 2.x, and Alembic are already decided; this document does not reopen any of them.

What's left, and what this document owns exclusively:

- **Type mapping** — the exact PostgreSQL/TimescaleDB column type for every category of DOC-012 field, with precision and scale stated as numbers, not adjectives.
- **Storage assignment made physical** — which DOC-012 schema lands in which literal table, in which database, matching DOC-011's `persistence/postgres/{models,facts,outcomes_insights}.py` / `persistence/timescale/repositories.py` split exactly.
- **Indexing** — which index serves which real query pattern already implied by another document (the Finality Engine, the PIT filter, a research dashboard).
- **Partitioning and compression** — the three TimescaleDB hypertables, named, with their partitioning column and a compression policy.
- **Migration policy** — what Alembic is and is not allowed to do to a row that DOC-012 or DOC-013 has already called immutable.

A rule belongs here only if getting it wrong produces a real, physical failure mode — silent truncation, a slow table scan, an unrecoverable migration — not a documentation inconsistency. That is the test for every section below.

---

# Type Mapping Rules

Four categories cover every field in DOC-012. A field's category is determined by what DOC-012 already says it *is*, not by guessing from its Python type alone — `str` alone is not enough information, and treating every `str` the same is exactly how a Token Amount and a display string end up in the same column type.

| Category | DOC-012 signal | PostgreSQL / TimescaleDB type | Reasoning |
|---|---|---|---|
| **Token Amount** (raw, on-chain, always an integer in the smallest denomination) | Fields DOC-012 or DOC-008 explicitly calls "Token Amount" — `total_supply`, `reserve0`/`reserve1`, `amount0`/`amount1`, `amount0_in`/`amount1_in`/`amount0_out`/`amount1_out`, `liquidity_delta`, `volume_base`/`volume_quote`, `buy_volume`/`sell_volume` | **`NUMERIC(78, 0)`** | `uint256`'s maximum value is `2^256 - 1 ≈ 1.157 × 10^77` — 78 decimal digits. Scale is `0` because decimals are never pre-applied (DOC-008: Human Amount is derived, Raw Amount is canonical) — these are always whole numbers in the smallest unit. A narrower `NUMERIC` here is a silent-truncation bug waiting for a token with unusually large supply. |
| **Price / Ratio / Derived-USD** (computed, not itself an on-chain integer) | `price`, `open`/`high`/`low`/`close`, `vwap`, `liquidity_usd`, `market_cap_usd`, `fdv_usd` | **`NUMERIC`** (unconstrained precision/scale) | These are ratios of two Token Amounts with potentially very different `decimals`, so neither the integer part nor the fractional part has a fixed safe bound the way a raw amount does. Postgres `NUMERIC` with no declared precision/scale stores arbitrary precision natively — the correct choice when *no* fixed scale is safe, rather than picking one that will eventually be wrong for some token pair. |
| **Genuinely float** (DOC-012's own categorical test: computed by an engine, not a pass-through, and dimensioned or not) | `Feature.value`, `Blockchain.avg_block_time_seconds` | **`DOUBLE PRECISION`** | DOC-012 already ruled these `float` at the Pydantic level specifically for Polars vectorization; storing them as anything else in the database would force exactly the Decimal-parsing cost on read that DOC-012 avoided on write. `DOUBLE PRECISION` is Postgres's IEEE-754 64-bit float — the same representation, no silent widening or narrowing. |
| **Everything else** | identifiers, enums, timestamps, booleans, free text, small structured data | see table below | Standard, low-risk mappings — listed for completeness, not because any of them are contentious. |

### Standard mappings (category 4)

| DOC-012 type | PostgreSQL type | Notes |
|---|---|---|
| Canonical ID / `fact_id` / any `str` identifier | `TEXT` | Never `VARCHAR(n)` with an arbitrary limit — Canonical IDs (`eip155:<chain_id>/<type>:<address>`) have no fixed maximum worth enforcing at the schema level. |
| EIP-55 address fields (`contract_address`, `pool_address`, wallet `address`) | `VARCHAR(42)` | The one exception to the rule above — a checksummed EVM address is always exactly 42 characters (`0x` + 40 hex). A fixed-width column here catches a malformed address at insert time. |
| `tx_hash`, `block_hash` | `VARCHAR(66)` | `0x` + 64 hex characters, always — same reasoning. |
| `enum` fields (`confirmation_status`, `fact_type`, `verification_status`, `contract_type`, `outcome_type`, `importance`, `interval`) | Native Postgres `ENUM` type | Rejects an invalid value at the database layer too, not only in Pydantic — a second, independent line of defense that costs nothing at this scale. |
| `int` — block numbers, `confirmations`, `log_index` | `BIGINT` | Block numbers are monotonically increasing forever; `INTEGER`'s ~2.1 billion ceiling is not a safe assumption to make on this project's time horizon. |
| `int` — small bounded counts (`holder_count`, `trade_count`, `decimals`, `fee_tier_bps`) | `INTEGER` | Bounded by real-world limits (`decimals` is `uint8`, `fee_tier_bps` is basis points ≤ 10,000) — `BIGINT` here is unwarranted width. |
| `datetime` (`event_time`, `observed_at`, `ingested_at`, and every other timestamp) | `TIMESTAMPTZ` | Never bare `TIMESTAMP`. DOC-008's Triple Timestamp Standard is meaningless if the database can't guarantee timezone-aware ordering across providers. |
| `bool` | `BOOLEAN` | |
| `dict[str, str]` (`social_links`) | `JSONB` | Small, schema-flexible, queried rarely and never at the row-filtering level. |
| `list[str]` (`tags`, `source_features`, `inputs`) | `TEXT[]` (native Postgres array) | Not `JSONB` — these are homogeneous lists of IDs, and a native array supports `@>`/`ANY()` queries without JSON operators. |
| `tuple[str, str]` (`source_fact_range`) | Two columns: `source_fact_range_start TEXT`, `source_fact_range_end TEXT` | Audit/display fields only — they record which two `fact_id`s bounded the predicate match, for a human or debugging tool to look up directly. They are **not** a mechanism for re-deriving bar membership: a `fact_id` embeds a `tx_hash`, which carries no chronological ordering, so a range comparison like `source_fact_range_start <= fact_id <= source_fact_range_end` would be meaningless — two facts can compare in either lexicographic order regardless of which happened first. The authoritative reconstruction predicate is DOC-012's own `event_time` + `pair_id` + `fact_type` + `confirmation_status` filter (§ MarketBar); these two columns never substitute for it. |

---

# The Discriminated Payload: JSONB, Not a Wide Table

`BlockchainFact.payload` is DOC-012's one field this document actually has to *design* a representation for, not just map — a discriminated union has no single obvious column type.

### Decision: `payload JSONB`

- **Chosen Because:** The shape genuinely varies by `fact_type`, and Pydantic (DOC-012's discriminated union, `Field(discriminator="fact_type")`) already validates that shape completely before a row is ever written. A `JSONB` column defers structural enforcement to the layer that already owns it, rather than duplicating it in the database.
- **Alternatives Considered:** (a) A wide table with a nullable column for every field across all four payload shapes; (b) a separate payload table per `fact_type`, joined by `fact_id`.
- **Why Rejected:** (a) produces a table that is mostly `NULL` for any given row and grows a new sparse column every time a `fact_type` is added — exactly the kind of schema the Domain Model is supposed to prevent leaking into. (b) is more normalized, but turns "read one Fact" into a conditional join on `fact_type`, and a new `fact_type` becomes a new table plus a migration, when DOC-012 already treats adding a `fact_type` as something the discriminated union should absorb without a schema fight.
- **This choice also directly serves DOC-012's own Schema Versioning Policy**, which requires that "old persisted records keep their original `schema_version` forever." Because `payload` is `JSONB`, a breaking change to one payload shape's fields never requires an `ALTER TABLE` at all — `schema_version` (a plain `TEXT` column) tells `processing/schema_dispatcher.py` which Pydantic model to validate against on read, and the column itself never has to change shape to accommodate it.
- **Migration Trigger:** If a specific payload field needs first-class SQL-level filtering at volume (e.g., "all swaps where `amount0_in` exceeds X" becomes a common, performance-sensitive query), add a Postgres **generated column** or an **expression index** on `(payload->>'amount0_in')::numeric`, scoped to that one field — not a redesign of the column itself.

## One field earns that treatment already: wallet involvement

The Migration Trigger above is written as a future "if" — but `/v1/wallets/{id}/activity` (DOC-015) ships as an MVP endpoint, not a deferred one, and it already needs exactly this. "A wallet participated in this Fact" is spelled differently per `fact_type` — `sender`/`recipient` inside a `SWAP_EXECUTED` payload, `provider` inside `LIQUIDITY_ADDED`/`LIQUIDITY_REMOVED` — and `PAIR_CREATED` has no wallet field at all. No single JSONB key covers all three, so the trigger condition above is already met, not merely anticipated.

**Decision:** a `STORED` generated column, computed once at write time:

```sql
involved_wallets TEXT[] GENERATED ALWAYS AS (
  CASE fact_type
    WHEN 'SWAP_EXECUTED'      THEN ARRAY[payload->>'sender', payload->>'recipient']
    WHEN 'LIQUIDITY_ADDED'    THEN ARRAY[payload->>'provider']
    WHEN 'LIQUIDITY_REMOVED'  THEN ARRAY[payload->>'provider']
    ELSE ARRAY[]::TEXT[]
  END
) STORED
```

with a `GIN` index on `involved_wallets` (see § Indexing Strategy). This is the one payload field getting a dedicated index ahead of the generic wait-for-volume policy above, precisely because a real MVP endpoint already has no other correct query path against three inconsistently-named JSONB keys.

---

# Storage Assignment — DOC-012's B.0–B.4, Made Physical

| DOC-012 Schema | Database | Table | Repository file (DOC-011) |
|---|---|---|---|
| `Blockchain`, `SmartContract`, `Token`, `TradingPair`, `LiquidityPool`, `Wallet`, `Metadata` (Part A) | PostgreSQL | one table each, conventional ORM-mapped | `persistence/postgres/models.py` |
| `Checkpoint` (§ B.0) | PostgreSQL | `checkpoints` — PK `chain_id`, mutable | `persistence/postgres/facts.py` |
| `BlockchainFact` (§ B.1) | PostgreSQL | `blockchain_facts` — PK `fact_id`, append-only | `persistence/postgres/facts.py` |
| `StateProjection` (§ B.2) | Redis | not a SQL table — see `transport/state_cache.py` (DOC-011) | *(none — out of scope for this document)* |
| `ObservationSnapshot`, `MarketBar`, `Feature` (§ B.3) | TimescaleDB | three hypertables, below | `persistence/timescale/repositories.py` |
| `Outcome`, `Insight` (§ B.4) | PostgreSQL | `outcomes`, `insights` — regular tables, not hypertables | `persistence/postgres/outcomes_insights.py` |

`Checkpoint` and `BlockchainFact` sharing one file (`facts.py`) but not one table is intentional and already stated in DOC-011 — repeated here only to confirm the physical table boundary matches the file boundary, since a reader of *this* document shouldn't have to cross-check DOC-011 to be sure.

---

# Indexing Strategy

Every index below exists because a specific, already-documented query pattern needs it — not as general-purpose "index the common columns" advice.

| Table | Index | Serves |
|---|---|---|
| `blockchain_facts` | `(chain_id, confirmation_status)` | `processing/finality_engine.py` re-checking every `PENDING`/`CONFIRMED` fact on a chain as new blocks arrive (ADR-006 § Canonical Chain Validation Engine) — without this, that scan is a full table scan on a table that is, by design, never small. |
| `blockchain_facts` | `(chain_id, block_number)` | Reorg resolution walking a specific block range when a chain of headers stops matching (ADR-006). |
| `blockchain_facts` | `GIN` on `involved_wallets` | `/v1/wallets/{id}/activity` (DOC-015) — membership queries (`involved_wallets @> ARRAY[:address]`) against the generated column above. Without this, that endpoint has no query path that isn't a full scan. |
| `trading_pairs` | `base_token_id`, `quote_token_id` (two separate indexes) | "Which pairs exist for this token" — a token is queried as base in some pairs and quote in others; a single combined index would only serve one direction. |
| `market_bars` (hypertable) | `(pair_id, interval, bar_start_time DESC)` | The primary research query: OHLCV history for one pair, one interval, over a time range. TimescaleDB's automatic time-partitioning handles the `bar_start_time` range; this index adds the `pair_id, interval` filter TimescaleDB doesn't infer on its own. |
| `features` (hypertable) | `(entity_id, feature_name, as_of_timestamp DESC)` | The Point-in-Time pattern: "the most recent value of Feature X for entity Y as of timestamp T" — DOC-015's `as_of_timestamp` query parameter resolves directly to this index. Without `DESC`, that query degrades to a scan-and-sort on every call. |
| `observation_snapshots` (hypertable) | `(entity_id, snapshot_timestamp DESC)` | Same PIT pattern, one schema over. |
| `outcomes` | `(entity_id, outcome_type, evaluation_timestamp DESC)` | Research querying "what happened to this entity" without needing every historical outcome scanned. |

No index is proposed for `insights` beyond its primary key — nothing elsewhere in this document set describes a query pattern that needs one yet, and an unused index is a write-cost with no offsetting benefit. Add one when a real query pattern, not a hypothetical one, asks for it.

---

# TimescaleDB Hypertables & Partitioning

Three hypertables, each with an explicit partitioning column and a compression policy — not implied by "TimescaleDB handles it."

| Hypertable | Partitioning column | Suggested chunk interval | Compression |
|---|---|---|---|
| `observation_snapshots` | `snapshot_timestamp` | `1 day` | Compress chunks older than `7 days` |
| `market_bars` | `bar_start_time` | `7 days` (lower write frequency than snapshots — a wider chunk avoids excessive small-chunk overhead) | Compress chunks older than `30 days` |
| `features` | `as_of_timestamp` | `1 day` | Compress chunks older than `7 days` |

Compression is not optional-and-someday — DOC-010 named "continuous aggregates and compression for OHLCV/Snapshots" as a reason TimescaleDB was chosen over a plain PostgreSQL table in the first place (DOC-010 § Storage). An uncompressed hypertable is TimescaleDB used as a slower PostgreSQL, forfeiting the actual reason it was selected.

**Compressed chunks are still `FINALIZED`-row-immutable** (DOC-013 § Immutability & State Modeling) — TimescaleDB's compression is a storage-format change, not a data change, and does not require decompressing a chunk to satisfy the row-level immutability guard, which lives in application code (`persistence/postgres/facts.py`) and is untouched by whether the underlying chunk happens to be compressed.

Chunk intervals above are starting points, not permanent commitments — DOC-010's own principle applies here too: revisit only when actual chunk size or query latency, not a guess, says to.

---

# Migration Policy

Alembic (DOC-011) is the only migration tool, one linear history — but "Alembic runs the migration" is not the same question as "which migrations are safe."

**Part A entities** (Token, TradingPair, Wallet, and the rest) are conventional, mutable, operational data. Standard Alembic migrations — add a column, add an index, even a carefully-staged rename — are unremarkable here, the same as they would be in any operational schema.

**`blockchain_facts` (§ B.1) migrations are additive-only, without exception:**

- A new column may be added, always `NULL`-able with no default backfill assumption for existing rows.
- An existing column may **never** change type, be renamed, or be dropped.
- A `payload` shape change never touches this table at all — see § The Discriminated Payload above; that is precisely the property `JSONB` was chosen to provide.
- No migration may `UPDATE` a row where `confirmation_status = 'FINALIZED'`. This is the same guard DOC-013 § Immutability & State Modeling already places in application code — a migration is not exempt from it merely for running as a superuser, once, outside normal request flow. If a migration script needs to touch `FINALIZED` rows to fix a real data problem, that is an incident, not a routine migration, and should be treated with the same weight as a production data-repair operation anywhere else — logged, reviewed, never silent.

**`checkpoints` (§ B.0) has no such restriction** — it is a small, singleton-per-chain, always-mutable table, and ordinary migrations apply without qualification.

**Every migration is forward-only.** A `downgrade()` that would `DROP` a column or table containing any `blockchain_facts`, `outcomes`, or `insights` data is not written — if a migration needs reverting, it is reverted by a new forward migration, never by running history backward through data that, per DOC-006 and DOC-008, is not supposed to be capable of disappearing.

---

# Data Integrity Constraints (defense in depth)

DOC-012 and DOC-013 already enforce these rules at the Pydantic and application layer. The database constraints below are a second, independent line of defense — cheap at this scale, and exactly the kind of redundancy worth having around a project's core correctness guarantees.

- **Foreign keys where the reference is monomorphic.** `TradingPair.base_token_id` / `quote_token_id` always reference `Token.canonical_id` — a real `FOREIGN KEY` constraint here catches a broken reference at write time. `LiquidityPool.canonical_id` referencing its parent `TradingPair.canonical_id` is the same case.
- **No foreign key where the reference is polymorphic.** `Feature.entity_id`, `ObservationSnapshot.entity_id`, `Outcome.entity_id`, and `Insight.entity_id` can each point at a `TradingPair`, a `Wallet`, or a `Token` depending on `entity_type` — Postgres has no native polymorphic foreign key, and simulating one (a trigger checking three tables conditionally) adds real complexity for a guarantee the application layer (repository translation, DOC-011) already provides. Left as an application-level responsibility, explicitly, rather than silently unenforced and unexplained.
- **`CHECK` constraints for values Pydantic already validates but the database can cheaply double-check:** `confirmations >= 0`, `fee_tier_bps IS NULL OR fee_tier_bps BETWEEN 0 AND 10000`, `label_value IS NOT NULL` on a finalized `Outcome` row.
- **`CHECK (value >= 0)` on every Token Amount column** — `total_supply`, `reserve0`/`reserve1`, `amount0`/`amount1`, `amount0_in`/`amount1_in`/`amount0_out`/`amount1_out`, `liquidity_delta`, `volume_base`/`volume_quote`, `buy_volume`/`sell_volume`. A raw on-chain quantity is never negative (§ Type Mapping Rules); a negative value in any of these columns is a corrupted read, not a valid edge case worth silently accepting.
- **A `BEFORE UPDATE` trigger on `blockchain_facts` enforcing the `FINALIZED`-immutability rule at the database level** is a reasonable future addition — DOC-013 already says so — but is not required for the MVP. The guard in `persistence/postgres/facts.py` is the primary and sufficient enforcement for now; this is noted here only so the two documents don't quietly disagree about whether it's already required.

---

# Related Documents

| Document | Relevance |
|---|---|
| DOC-008 Canonical Glossary | Source of the Financial Precision Principle and Triple Timestamp Standard this document's type mappings implement. |
| DOC-010 Technology Stack | Chose PostgreSQL, TimescaleDB, SQLAlchemy 2.x, and Alembic — not reopened here. |
| DOC-011 Repository Structure | Defines the exact files (`persistence/postgres/*.py`, `persistence/timescale/repositories.py`) this document's Storage Assignment table maps onto. |
| DOC-012 Canonical Schema Specification | Source of every field this document maps — and the document that explicitly declined to do so itself. |
| DOC-013 Coding Standards | Source of the row-level immutability guard this document's Migration Policy extends to Alembic specifically. |
| ADR-006 | Source of the Checkpointing and Canonical Chain Validation behavior the `chain_id, confirmation_status` index and the Checkpoint table both exist to serve. |

---

# Guiding Principle

> A column type is not a design decision made here — DOC-012 already made it. This document's only job is making sure PostgreSQL and TimescaleDB agree with a decision that was never theirs to make in the first place.