# Architecture Overview

A concise view of the system's structure, data flow, storage, and the core design decisions. For the full specification see DOC-001..015 and ADR-006 in `docs/`.

## Executive Summary

Onchain Platform is an **AI-native quantitative research platform** that transforms raw blockchain activity into reproducible, point-in-time-correct research knowledge.

**Key characteristics:**
- **Modular monolith** with strict, mechanically-enforced dependency boundaries (import-linter, 8/8 contracts)
- **Deterministic processing** — identical inputs produce identical outputs (replay tests verify byte-for-byte)
- **Point-in-Time correctness** — derived values never look ahead of their `as_of` timestamp
- **Financial precision** — monetary values are `Decimal`/`str`, never `float`
- **Explainable** — every ranking and insight traces to its inputs (DOC-001)

## System Architecture

```
External Sources (EVM RPC endpoints)
        │
        ▼
   Data Acquisition (acquisition/)  — providers, collector
        │
        ▼
   Data Processing (processing/)    — normalizer, fact_processor, finality_engine (reorg handling)
        │
        ▼
   Domain Management (domain_management/)  — entity resolution, wallet, metadata
        │
        ▼
   Market Analytics (analytics/)    — projection, trade_aggregator (bars), feature_engine, outcome_engine
        │
        ▼
   Intelligence (intelligence/)     — GoPlus client, risk rules, insight generator
        │
        ▼
   Strategy (strategy/)             — candidate ranking (deterministic, explainable)
        │
        ▼
   Research Platform (research/)    — FastAPI REST API + Streamlit dashboard
```

**Cross-cutting infrastructure** (importable by any capability, never importing one): `persistence/` (Postgres/Timescale + Redis), `transport/` (state cache, event streams), `platform/` (config, logging, scheduler).

## Data Flow

### 1. Ingestion
```
Blockchain events → Raw logs (acquisition/) → Normalized events (processing/)
  → Blockchain Facts (persistence) → Confirmation lifecycle (Pending → Confirmed → Finalized)
  → Orphaned on reorg (events, never deletes)
```

### 2. Analytics
```
Finalized facts → State Projection (Redis) → Observation snapshots → Market bars (from swap facts)
  → Features (PIT-correct) → Outcomes (labeled once a window closes)
```

### 3. Research
```
Features + Outcomes → Strategy ranking → Research datasets → API / dashboard
```

## Storage Strategy

- **PostgreSQL / TimescaleDB** (single service `timescaledb`, port 5433): operational entities (`tokens`, `trading_pairs`, `wallets`, `outcomes`, `insights`) **and** time-series hypertables (`market_bars`, `observation_snapshots`, `features`) with chunking + compression policies.
- **Redis** (port 6379): state cache (`state:*`), GoPlus rate limiting/caching, reorg dedup.

## Dependency Management

Enforced by `import-linter` in `pyproject.toml` (DOC-011). The layer order (top can import below, per the `layers` contract) is:

```
strategy → research → intelligence → analytics → domain_management → processing → acquisition → domain
```

Plus `forbidden` contracts closing the gaps (e.g. `research/` may not import `strategy/`; `analytics/` may not import `intelligence/`). `persistence/`, `transport/`, `platform/` are cross-cutting and must never import a capability. `main.py` is the composition root — the only file allowed to see multiple capabilities (exempt).

## Key Design Decisions

1. **Deterministic processing** — no wall-clock in capabilities, no unseeded randomness, ordered iteration only. Result: replay produces identical outputs.
2. **Financial precision** — `Decimal` for all on-chain amounts/prices; JSON `string`; DB `NUMERIC`. `Feature.value` is the deliberate exception (`float`, for Polars).
3. **Point-in-time correctness** — features use only data ≤ `as_of_timestamp`; one code path for backtest and live (`get_feature_at`).
4. **Immutable facts** — append-only; no update once `FINALIZED` (one legal transition to `ORPHANED`).
5. **Provider independence** — abstract `BlockchainProvider` interface; no vendor code in business logic.
6. **Capability-owned routers** — `strategy/` owns its own FastAPI router; wired via `create_app(extra_router=...)` so `research/` never imports `strategy/`.

## PIT Correctness

`Feature.entity_id` + `feature_name` + `as_of_timestamp` (descending) is the query index. `get_feature_at(session, entity_id, feature_name, as_of)` filters `as_of_timestamp <= as_of` and returns the most recent row — the same function serves backtests and live queries, which is what makes research reproducible (DOC-013).

## Failure Handling & Recovery

- **Checkpointing** — `checkpoints` table records last finalized block per chain; restart resumes from checkpoint (ADR-006).
- **Replay** — state is always rebuildable by replaying finalized facts.
- **Reorgs** — handled as a Domain Event (`ChainReorgEvent`), never an exception; affected facts marked `ORPHANED`, market bars recomputed from the predicate (never patched).

## Quality Gates

All code must pass:
```bash
make lint          # ruff
make typecheck     # mypy strict
make import-check  # import-linter, 8/8 KEPT
make test          # unit + integration + schema
make test-replay   # determinism
```

## References

- [DOC-004 Architecture](../docs/004-Architecture.md)
- [DOC-006 Domain Model](../docs/006-DomainModel.md)
- [DOC-010 Technology Stack](../docs/010-TechStack.md)
- [DOC-011 Repository Structure](../docs/011-RepositoryStructure.md)
- [DOC-012 Canonical Schemas](../docs/012-CanonicalSchema.md)
- [DOC-013 Coding Standards](../docs/013-CodingStandards.md)
- [ADR-006 Blockchain Data Acquisition](../docs/adr/ADR-006-Blockchain-Data-Acquisition-Strategy.md)