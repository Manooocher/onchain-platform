---
name: persistence
description: Use when writing or modifying anything under src/onchain_platform/persistence/** or migrations/**, or when a task involves a database column type, index, or migration. Full spec: docs/014-PersistencePolicy.md.
---

# Persistence — PostgreSQL & TimescaleDB

- Token Amounts (raw, integer, smallest denomination): `NUMERIC(78, 0)`. Prices, ratios, and derived-USD values: unconstrained `NUMERIC`. `Feature.value` only: `DOUBLE PRECISION`. Nothing else in this schema uses a bare float column.
- `blockchain_facts` migrations are additive-only: a new column may be added (always nullable), but an existing column is never altered, renamed, or dropped. `payload` is `JSONB` specifically so a new `fact_type` or a breaking payload change never needs a schema migration at all — `schema_version` plus `processing/schema_dispatcher.py` handle it.
- Never `UPDATE` a row where `confirmation_status = 'FINALIZED'` — not in application code, not in a migration script, not as a superuser doing a one-off fix. That's an incident, not a routine change.
- `checkpoints` has no such restriction — small, mutable, singleton-per-chain, ordinary migrations apply.
- `persistence/postgres/{models,facts,outcomes_insights}.py` are the only files in the repo allowed to know what a SQLAlchemy model looks like. `repositories.py` is the translation boundary: it accepts and returns `domain/` types, never leaks an ORM instance upward.
- Foreign keys where the reference is monomorphic (`TradingPair.base_token_id` → `Token.canonical_id`). No FK where it's polymorphic (`Feature.entity_id` can be a `TradingPair`, `Wallet`, or `Token`) — that stays an application-level responsibility, not a simulated constraint.
