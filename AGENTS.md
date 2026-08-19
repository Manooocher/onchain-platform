# onchain_platform

AI-native quant research platform for on-chain data. EVM-only MVP (Ethereum, Base, BNB Chain). Full architecture lives in `docs/001-Vision.md` through `docs/015-APIContracts.md`, `docs/adr/ADR-006-Blockchain-Data-Acquisition-Strategy.md`, and `docs/implementation/ImplementationPlan.md`. Read the relevant one before working in an area for the first time — the skill for that area (`.opencode/skills/`) surfaces automatically when a task is about it, so you often won't need to ask.

## Non-negotiable invariants (apply everywhere, no exceptions)

- `domain/` imports nothing else in this repo. Every other package imports from it, never the reverse. Enforced by `import-linter` (`make import-check`) — DOC-011 § Enforcing the Dependency Rule.
- Money is `Decimal`/`str`, never `float` — except `Feature.value` and `Blockchain.avg_block_time_seconds`, which are `float` on purpose (DOC-012 § Clarifying an ambiguity in DOC-008). About to type `float` for anything touching an amount or price? Stop and check that section first.
- A `blockchain_facts` row is immutable once `confirmation_status = FINALIZED`, except the one legal transition to `ORPHANED` (DOC-013 § Immutability & State Modeling, DOC-014 § Migration Policy). No migration, no admin script, no "just this once."
- No `datetime.now()` / `time.time()` inside Capability logic. Time is a parameter or a Canonical Schema field (DOC-013 § Determinism Discipline). This is the whole reason Replay Tests can exist.
- A `BlockchainFact` payload, or any Canonical Schema, never crosses a Capability boundary as anything other than its `domain/schemas/` or `domain/entities/` shape. No parallel DTOs, no bespoke API response models (DOC-012, DOC-015 § Response Shape).
- Reorgs are a `ChainReorgEvent` (DOC-012 § B.5) published to Redis Streams, never raised as a Python exception (DOC-013 § Exception Hierarchy).
- Any exception that would cross a Capability boundary is translated to a `PlatformError` subclass first (DOC-013 § Exception Hierarchy) — never let a raw SQLAlchemy or httpx exception propagate out of `persistence/` or `acquisition/`.

## Commands

- `make lint` — ruff check + format check
- `make typecheck` — mypy
- `make test` — unit + integration + schema tests (fast — run constantly)
- `make test-replay` — replay tests (slow, needs fixture data — required before any PR touching `processing/` or `analytics/`)
- `make import-check` — the import-linter contracts (DOC-011)
- `make run` — `docker compose up` (Postgres+TimescaleDB, Redis)

## Where things are

Full map: DOC-011 § `src/onchain_platform/` Package Layout. Short version — strict dependency order, enforced mechanically, not just by convention:

```
acquisition/ → processing/ → domain_management/ ┬→ analytics/ → intelligence/ ┬→ research/ → strategy/
                                                  └──────────────────────────┘
```

`domain/` sits underneath all of it and depends on nothing. `persistence/`, `transport/`, `platform/` may be imported by any Capability above but never import one back. `main.py` is the only file allowed to see more than one Capability at once — it's the composition root, exempt from the import-linter contracts because it's wiring, not logic. Keep it that way.

## Current milestone

Check `docs/implementation/ImplementationPlan.md` before starting new work. Build one vertical slice at a time — walking skeleton, not parallel half-finished Capabilities. Don't start Milestone N+1 work while Milestone N doesn't pass `make test-replay` yet.

## Agents available in this project

`planner` (read-only, plans before code — invoke first for anything non-trivial), `spec-auditor` (checks a diff against the doc it claims to implement), `correctness-hunter` (checks correctness independent of doc compliance, deliberately on a different model family), `doc-consistency-auditor` (checks docs against each other, run at the end of a milestone, not per commit). See `.opencode/agent/*.md` for exact scope and `docs/implementation/ImplementationPlan.md` for what "current milestone" means at any given time.
