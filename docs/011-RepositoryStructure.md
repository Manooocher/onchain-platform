---
id: DOC-011
title: Repository Structure
version: 1.5
status: Draft
owner: CTO
last_updated: 2026-08-19
tags:
  - repository
  - engineering
  - structure
  - implementation-policy
related_docs:
  - DOC-004 Architecture
  - DOC-006 Domain Model
  - DOC-008 Canonical Glossary
  - DOC-009 System Capabilities
  - DOC-010 Technology Stack
  - DOC-012 Canonical Schema Specification
  - DOC-014 Persistence Policy
  - ADR-006 Blockchain Data Acquisition Strategy
---

# Repository Structure

> The repository is the Architecture (DOC-004) made physical.
>
> If a folder cannot be explained by a Capability (DOC-009), a Pipeline (DOC-007), or a Technology Decision (DOC-010), it should not exist.

> **Placeholder notice:** this document uses `onchain_platform` as the package name. No project name has been decided yet. Replace every occurrence with the real package name in one pass before scaffolding.

---

# Purpose

This document defines where code lives, not what it does. The *what* is already defined:

- **Domain concepts** → DOC-006, DOC-008
- **Capabilities** → DOC-009
- **Pipelines** → DOC-007
- **Technologies** → DOC-010
- **Ingestion architecture** → ADR-006

This document's only job is to map those decisions onto a physical folder tree, so that a Senior Engineer opening the repository for the first time can find any concept above in under thirty seconds — and so that the dependency rules already agreed upon (domain purity, provider replaceability, Canonical Schemas as the only contract) are enforced by the folder structure itself, not just by convention.

---

# Guiding Principles

- **The Domain Model has no friends.** `domain/` depends on nothing else in this repository. Everything else depends on it. This is the single rule every other decision below exists to protect.
- **Layout mirrors DOC-009, not developer habit.** Package names under `src/` map onto Capabilities and Pipelines by name, so a reader can go from the Capability Map straight to a folder without translation.
- **One repository, one deployable unit.** The MVP is a modular monolith (DOC-004). There is no multi-package monorepo tooling, no service-per-folder split. Module boundaries are enforced by import rules, not by network calls.
- **Tests are a first-class capability, not an afterthought.** The four test categories in DOC-010 (Unit, Integration, Replay, Schema) each get their own top-level directory because they run under different conditions and different frequencies.
- **Nothing here is permanent.** Per DOC-010's own principle — technologies are placeholders — this structure is expected to gain new leaf packages as Roadmap phases (DOC-005) unlock new Capabilities. It should not need to be reshaped to do so.

---

# Top-Level Layout

```text
onchain_platform/
├── src/
│   └── onchain_platform/
│       ├── domain/
│       ├── acquisition/
│       ├── processing/
│       ├── domain_management/
│       ├── analytics/
│       ├── intelligence/
│       ├── strategy/
│       ├── research/
│       ├── persistence/
│       ├── transport/
│       ├── platform/
│       └── main.py        # composition root — see § Composition Root
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── replay/
│   └── schema/
├── migrations/
├── config/
├── docs/
│   └── adr/
├── scripts/
├── docker/
├── .github/
│   └── workflows/
├── pyproject.toml
├── uv.lock
├── docker-compose.yml
├── .env.example
├── .gitignore
├── Makefile
└── README.md
```

Why `src/` and not a flat package at the repository root: with `uv` and modern packaging, `src/`-layout forces every import — including your own tests — to go through the installed package rather than accidentally resolving to a local file. This is the same "no implicit access" discipline DOC-010 already applies to ORM models and provider SDKs; it costs nothing and prevents an entire category of "works on my machine" bugs.

---

# `src/onchain_platform/` — Package Layout

## `domain/`

```text
domain/
├── schemas/           # Pydantic Canonical Schemas — field shapes defined in DOC-012 Part B, concepts in DOC-008
│   ├── enums.py       # ConfirmationStatus, FactType — fact-lifecycle enums (DOC-012 § B.1)
│   ├── checkpoint.py
│   ├── blockchain_fact.py
│   ├── state_projection.py
│   ├── observation_snapshot.py
│   ├── market_bar.py
│   ├── feature.py
│   ├── outcome.py
│   ├── insight.py
│   └── chain_reorg_event.py
├── entities/          # DOC-012 Part A — the slowly-changing Structural Domain (DOC-006)
│   ├── blockchain.py
│   ├── smart_contract.py
│   ├── token.py
│   ├── trading_pair.py
│   ├── liquidity_pool.py
│   ├── wallet.py
│   └── metadata.py
├── enums.py           # ChainId, EntityType — structural/registry enums (slowly-changing, Part A)
└── ids.py             # Canonical ID construction (eip155:<chain_id>/<entity_type>:<address>)
```

**Depends on:** nothing else in this repository. Not `persistence`, not `acquisition`, not a specific database or RPC library.

This is the direct implementation of DOC-006's own rule: *"Infrastructure depends on the Domain. The Domain never depends on Infrastructure."* Every other package below imports from `domain/`; `domain/` imports from none of them. This single directional rule is what makes every technology in DOC-010 replaceable without a Domain Model rewrite — it is worth enforcing mechanically (see § Enforcing the Dependency Rule) rather than trusting code review alone.

`schemas/` and `entities/` are not an arbitrary split — they mirror DOC-012 Part A (Entity Schemas) and Part B (Temporal Schemas) exactly, all six of Part B's storage-fate subsections (§ B.0–B.5) included, not only the ones with a table behind them. If a new field belongs to a slowly-changing registry object, it is typed in `entities/`; if it is Checkpoint/Fact/State/Snapshot/Bar/Feature/Outcome/Insight/Event-shaped, it is typed in `schemas/`. DOC-012 is the source of truth for which is which — this folder split should never drift from it, and `checkpoint.py` missing from this exact list until now is the drift this sentence exists to prevent.

---

## `acquisition/` — Capability: Data Acquisition (DOC-009, ADR-006)

```text
acquisition/
├── providers/
│   ├── base.py           # abstract BlockchainProvider interface
│   ├── alchemy.py
│   ├── quicknode.py
│   ├── infura.py
│   ├── chainstack.py
│   └── local_node.py
├── collector.py          # block/log subscription, minimal transformation
└── checkpoint.py         # READ-ONLY here: loads last_finalized_block per chain to know where to resume (ADR-006 § Checkpointing). Writing/advancing the checkpoint is processing/finality_engine.py's job, not this file's — only the finality engine knows when a block is actually finalized.
```

Nothing outside `acquisition/providers/base.py`'s interface should ever be imported by another package. `main.py` wires a concrete provider (e.g., `AlchemyProvider`) to the interface at startup — this is the only place a vendor name should appear outside this folder. See § Composition Root for `main.py`'s full role and why it sits outside every contract in § Enforcing the Dependency Rule.

---

## `processing/` — Capability: Data Processing (DOC-009, ADR-006)

```text
processing/
├── normalizer.py         # provider payload → canonical shape
├── schema_dispatcher.py  # routes schema_version to the correct parser
├── fact_processor.py     # canonical shape → Blockchain Fact (Pending)
└── finality_engine.py    # Confirmation Lifecycle, multi-block reorg detection (ADR-006). Owns writing/advancing the checkpoint acquisition/checkpoint.py reads — finalization is decided here, nowhere else.
```

`finality_engine.py` is the code with the highest correctness bar in the repository — it is what the Replay Tests in `tests/replay/` exist to protect.

---

## `domain_management/` — Capability: Domain Management (DOC-009)

```text
domain_management/
├── entity_resolution.py  # Token / Trading Pair / Liquidity Pool / Smart Contract resolution
├── wallet_service.py     # Wallet resolution — DOC-006 Ownership table names "Wallet Service" as a distinct owner from Entity Resolution; kept separate here for that reason
└── metadata_service.py   # website, socials, verification status — "Buy" side of ADR-006
```

---

## `analytics/` — Capability: Market Analytics (DOC-009)

```text
analytics/
├── projection_engine.py  # Blockchain Facts → State Projection
├── trade_aggregator.py   # finalized SwapExecuted Facts → Market Bars (OHLCV)
├── feature_engine.py     # Observation Snapshots + Market Bars → Features
└── outcome_engine.py     # Observation Window + Rule Evaluation → Outcome
```

`trade_aggregator.py` must only ever import Facts, never Observation Snapshots — this is the exact rule from DOC-006 that prevents OHLCV sampling errors. Worth a code comment pointing back to that section, since it is a rule that reads as "obviously true" until someone in a hurry wires it up wrong.

---

## `intelligence/` and `strategy/` — Capabilities: Intelligence, Strategy (DOC-009, Limited/MVP scope)

```text
intelligence/
├── risk_rules.py         # deterministic rule engine; consumes bought commodity data (GoPlus, etc.)
└── insight_generator.py  # produces Insight (DOC-012 § B.4) from risk rules and Feature output — Suspicious Liquidity Growth, Whale Accumulation, etc.

strategy/
└── ranking.py            # candidate ranking/screening for research — not portfolio management
```

Both packages are intentionally thin for the MVP. `strategy/ranking.py` in particular should stay scoped to *research candidate ranking*; if it grows toward anything resembling portfolio allocation, that is a signal it has crossed into Non-Goal territory (DOC-003) and needs a conversation, not just a PR.

`insight_generator.py`'s placement here is a pragmatic call, not an unambiguous one: DOC-006's Ownership table names "Analytics Engine" as Insight's owner, but neither DOC-009 nor the Architecture module mapping actually defines an "Analytics Engine" as distinct from `analytics/`'s Feature/Projection engines. Intelligence is where it lands because DOC-006's own example Insights (Suspicious Liquidity Growth, Whale Accumulation) read as risk/pattern findings, not feature computation. Worth reconciling the DOC-006 wording in a future pass rather than leaving two documents quietly disagreeing.

---

## `research/` — Capability: Research Platform (DOC-009)

```text
research/
├── api/                  # FastAPI routers, OpenAPI schema
└── dashboard/            # Streamlit app (DOC-010 — temporary MVP UI)
```

---

## `persistence/`, `transport/`, `platform/` — Cross-Cutting Infrastructure

```text
persistence/
├── postgres/
│   ├── models.py             # SQLAlchemy ORM — operational entities only (DOC-012 Part A: Token, TradingPair, LiquidityPool, Wallet, SmartContract, Metadata)
│   ├── facts.py              # SQLAlchemy ORM — BlockchainFact (DOC-012 § B.1, append-only) + Checkpoint (DOC-012 § B.0, mutable singleton per chain) — two different mutability semantics in one file, intentionally: both are Postgres-resident ingestion state that nothing outside acquisition/processing should touch directly
│   ├── outcomes_insights.py  # SQLAlchemy ORM — Outcome + Insight (DOC-012 § B.4)
│   └── repositories.py       # translates domain schemas ↔ ORM models, across all three files above
└── timescale/
    └── repositories.py       # Observation Snapshots, Market Bars, time-series Features (DOC-012 § B.3)

transport/
├── event_stream.py       # Event Transport Layer — Redis Streams, Consumer Groups, backpressure (DOC-010 § Event Transport). Publishes DOC-012 § B.5 Domain Events (e.g. ChainReorgEvent) — the same file, not a separate one, since a Domain Event is exactly what this layer already exists to carry.
└── state_cache.py        # Current State cache — the Redis-backed store behind StateProjection (DOC-012 § B.2)

platform/
├── config.py             # Pydantic Settings, loads .env
├── logging.py            # structlog configuration
└── scheduler.py          # exposes the APScheduler instance and generic job-registration helpers. Does NOT itself import or know about Outcome Generation or any other Capability job — see § Composition Root for why, and for where that wiring actually happens.
```

`persistence/postgres/{models,facts,outcomes_insights}.py` are the *only* files in the repository allowed to know what a SQLAlchemy model looks like. Per DOC-010: *"Business Logic must never see ORM models."* `repositories.py` is the translation boundary — it accepts and returns `domain/` types (entities or schemas), never leaks a model instance upward. The three-way split mirrors DOC-012's own B.0/B.1/B.4 storage distinction exactly, so "which file has this model" is never a guess: operational entity → `models.py`, a Checkpoint or `BlockchainFact` → `facts.py`, `Outcome`/`Insight` → `outcomes_insights.py`.

`transport/` splits for the same reason `persistence/postgres/` does: `event_stream.py` is transient (Redis Streams, consumer groups, replayable-on-loss per ADR-006 § Failure Recovery), while `state_cache.py` backs a Canonical Schema (`StateProjection`) with its own read/write shape. One file was quietly doing two jobs with two different failure semantics.

---

# `tests/` — Mirrors DOC-010 § Testing Exactly

```text
tests/
├── unit/               # mirrors src/ package-for-package
├── integration/        # Collector → Fact Processor → Persistence, against real Postgres/Redis
├── replay/
│   ├── fixtures/       # fixed, known historical blockchain data slices
│   └── test_replay.py  # asserts byte-identical output vs. stored baseline (ADR-006 Principle 2)
└── schema/
    └── test_canonical_schemas.py   # hypothesis property-based tests per Canonical Schema
```

Replay tests are the regression suite for the platform's central promise, so they deserve their own CI job rather than living inside `integration/` — they are slower, they need committed historical fixture data, and a failure here means "reproducibility broke," a different severity than "a service call failed."

---

# Supporting Directories

```text
migrations/       # Alembic migrations — PostgreSQL + TimescaleDB schema, versioned
config/
└── confirmation_depth.yaml   # per-chain confirmation depth (ADR-006) — YAML, never business logic
docs/
├── 001-Vision.md ... 012-CanonicalSchema.md
└── adr/
    └── ADR-006-Blockchain-Data-Acquisition-Strategy.md
scripts/          # dev/ops tooling only (DOC-010: Bash "never part of a production processing pipeline")
docker/
├── Dockerfile
└── docker-compose.yml         # postgres+timescale, redis, platform — three services, no more
```

Moving `docker-compose.yml` under `docker/` vs. the repo root is a matter of taste — either is defensible. Pick root if you want `docker compose up` to work with zero flags; pick `docker/` if you expect more Docker-related files later. Noted here as a genuine either-way choice, not a strong recommendation.

### Makefile

Targets wrap the tools DOC-010 already commits to — nothing here introduces a new decision:

| Target | Runs |
|---|---|
| `make install` | `uv sync` |
| `make lint` | `ruff check .` + `ruff format --check .` |
| `make typecheck` | `mypy src/` |
| `make test` | `pytest tests/unit tests/integration tests/schema` |
| `make test-replay` | `pytest tests/replay` (kept separate — slower, needs fixture data; see § `tests/`) |
| `make import-check` | `lint-imports` (the § Enforcing the Dependency Rule contracts) |
| `make run` | `docker compose up` |
| `make migrate` | Alembic upgrade against the running Postgres/TimescaleDB containers |

`make test` deliberately excludes `tests/replay/` — bundling it in would make the everyday inner-loop command slow enough that people stop running it.

---

# Capability & Infrastructure → Directory Map

| Capability (DOC-009) | Directory |
|---|---|
| Data Acquisition | `acquisition/` |
| Data Processing | `processing/` |
| Domain Management | `domain_management/` |
| Market Analytics | `analytics/` |
| Intelligence | `intelligence/` |
| Strategy | `strategy/` |
| Research Platform | `research/` |

This table is a reference *into* DOC-009 — the seven rows above must match DOC-009's Capability Overview table exactly. If they ever diverge, DOC-009 is the canonical source; fix this table, not that one.

### Cross-Cutting Concerns

Not DOC-009 Capabilities — infrastructure every capability above depends on.

| Concern | Directory |
|---|---|
| Persistence | `persistence/` |
| Event Transport & State Cache | `transport/` |
| Configuration, Logging, Scheduling | `platform/` |

**AI Platform** does not have a directory. It is a DOC-009 Future Capability, deferred to DOC-005 Roadmap Phase 6 — see DOC-010 § AI Development Infrastructure. Adding a package for it before then would itself be a signal worth stopping on (see § What Does Not Belong Here).

---

# Enforcing the Dependency Rule

Stating "the Domain never depends on Infrastructure" as a principle (DOC-006) is not the same as guaranteeing it holds after the fortieth pull request. Below is the actual `import-linter` contract, derived directly from DOC-009's Capability Dependencies table — not an abstract description of one.

Add to `pyproject.toml`:

```ini
[tool.importlinter]
root_package = "onchain_platform"

[[tool.importlinter.contracts]]
name = "Capability dependency order (DOC-009 Capability Dependencies)"
type = "layers"
layers = [
    "onchain_platform.strategy",
    "onchain_platform.research",
    "onchain_platform.intelligence",
    "onchain_platform.analytics",
    "onchain_platform.domain_management",
    "onchain_platform.processing",
    "onchain_platform.acquisition",
    "onchain_platform.domain",
]

[[tool.importlinter.contracts]]
name = "Data Processing may only depend on Data Acquisition"
type = "forbidden"
source_modules = ["onchain_platform.processing"]
forbidden_modules = [
    "onchain_platform.domain_management",
    "onchain_platform.analytics",
    "onchain_platform.intelligence",
    "onchain_platform.research",
    "onchain_platform.strategy",
]

[[tool.importlinter.contracts]]
name = "Domain Management may only depend on Data Processing"
type = "forbidden"
source_modules = ["onchain_platform.domain_management"]
forbidden_modules = [
    "onchain_platform.acquisition",
    "onchain_platform.analytics",
    "onchain_platform.intelligence",
    "onchain_platform.research",
    "onchain_platform.strategy",
]

[[tool.importlinter.contracts]]
name = "Market Analytics may only depend on Data Processing and Domain Management"
type = "forbidden"
source_modules = ["onchain_platform.analytics"]
forbidden_modules = [
    "onchain_platform.acquisition",
    "onchain_platform.intelligence",
    "onchain_platform.research",
    "onchain_platform.strategy",
]

[[tool.importlinter.contracts]]
name = "Intelligence may only depend on Market Analytics and Domain Management"
type = "forbidden"
source_modules = ["onchain_platform.intelligence"]
forbidden_modules = [
    "onchain_platform.acquisition",
    "onchain_platform.processing",
    "onchain_platform.research",
    "onchain_platform.strategy",
]

[[tool.importlinter.contracts]]
name = "Research Platform may only depend on Market Analytics and Intelligence"
type = "forbidden"
source_modules = ["onchain_platform.research"]
forbidden_modules = [
    "onchain_platform.acquisition",
    "onchain_platform.processing",
    "onchain_platform.domain_management",
    "onchain_platform.strategy",
]

[[tool.importlinter.contracts]]
name = "Strategy may only depend on Research Platform"
type = "forbidden"
source_modules = ["onchain_platform.strategy"]
forbidden_modules = [
    "onchain_platform.acquisition",
    "onchain_platform.processing",
    "onchain_platform.domain_management",
    "onchain_platform.analytics",
    "onchain_platform.intelligence",
]

[[tool.importlinter.contracts]]
name = "Cross-cutting infrastructure never imports a Capability package"
type = "forbidden"
source_modules = [
    "onchain_platform.persistence",
    "onchain_platform.transport",
    "onchain_platform.platform",
]
forbidden_modules = [
    "onchain_platform.acquisition",
    "onchain_platform.processing",
    "onchain_platform.domain_management",
    "onchain_platform.analytics",
    "onchain_platform.intelligence",
    "onchain_platform.research",
    "onchain_platform.strategy",
]
```

An earlier version of this section had a `type = "independence"` contract with a single module in it — that is a no-op: `independence` checks that the *listed* modules don't import each other, and with only one module listed there was nothing to check against. `domain`'s actual independence guarantee comes from a different place below.

Two kinds of contract, two different guarantees, and the reasoning for why both are necessary:

1. **`layers`** — a strict total order, `domain` now included as the true bottom rung (below `acquisition`, not a separate contract). This gives two things at once: `domain` can never import anything else in the list (nothing is lower), and no capability can import anything listed above it. That is the cycle-prevention and "no upward imports" guarantee, in one contract.
2. **The six `forbidden` contracts** — `layers` alone is not enough, and claiming otherwise was this section's actual bug. A `layers` contract only forbids *upward* imports; it does not stop a higher layer from reaching *past* its immediate dependency to something further down that DOC-009 never sanctioned — e.g. `layers` alone would still let `analytics` import `acquisition` directly, even though DOC-009 only lists `analytics` as depending on Data Processing and Domain Management. Each `forbidden` contract above closes exactly that gap for one capability, matching its DOC-009 dependency row edge-for-edge.

`acquisition` gets no `forbidden` contract of its own — not an oversight, but because it needs none: as the bottom-most capability layer, `layers` already forbids it from importing any of the other six (there is nothing below it to skip to, so there is no gap left for a `forbidden` contract to close).

A violation of any contract fails CI the same way a Ruff or mypy error does — this is deliberately a third quality gate alongside those two (DOC-010 § Testing), not a separate process a reviewer has to remember to run.

---

# Composition Root — `main.py`

`src/onchain_platform/main.py` is where the seven Capability packages and three cross-cutting packages get wired together at startup. Its existence is not new — `acquisition/`'s section already mentioned it wires a concrete provider — but it never had an actual place in § Top-Level Layout, and its relationship to § Enforcing the Dependency Rule's contracts was never stated. Both gaps matter for the same reason:

`platform/scheduler.py` is described above as *not* importing any Capability — but DOC-010 is explicit that APScheduler's whole reason for existing is running jobs like Outcome Generation every 24h, and that job is `analytics/outcome_engine.py`'s code. Something, somewhere, has to import both `platform.scheduler` and `analytics.outcome_engine` to register that job. Contract 7 forbids `platform` from being that something. `main.py` is where it actually happens instead:

```python
# main.py — illustrative, not exhaustive
from onchain_platform.acquisition.providers.alchemy import AlchemyProvider
from onchain_platform.platform.scheduler import scheduler
from onchain_platform.analytics.outcome_engine import run_outcome_generation
from onchain_platform.research.api import create_app

provider = AlchemyProvider(...)              # wire a concrete provider to the interface
scheduler.add_job(run_outcome_generation, "interval", hours=24)  # wire a job to the scheduler
app = create_app()                            # mount the FastAPI app
```

`main.py` is exempt from every contract in § Enforcing the Dependency Rule — not by a special-case rule, but because none of the seven `source_modules` lists above name `onchain_platform.main`, and the `layers` contract only constrains the eight packages actually listed in it (the seven capabilities plus `domain`). This is worth stating outright rather than leaving implicit: a composition root is supposed to see everything, by definition, and a future contributor tightening these contracts should know not to accidentally fold `main.py` into one.

`main.py` stays wiring-only: instantiating providers, registering scheduled jobs, mounting the API app. Any actual logic that ends up here instead of in a Capability package is a sign this file is growing beyond its role.

---

# What Does Not Belong Here

- No business logic in `scripts/` — operational/dev tooling only (DOC-010).
- No provider-specific types (e.g., a raw `web3.py` `LogReceipt`) crossing out of `acquisition/providers/`. Everything crossing that boundary is a Canonical Schema.
- No SQLAlchemy model imported outside `persistence/`.
- No `.env` committed — `.env.example` only, with placeholder values.
- No AI/RAG/agent code — there is no package for it yet, matching DOC-010 § AI Development Infrastructure. Adding one prematurely would itself be a signal worth stopping on.
- No business logic in `main.py` — wiring only (providers, scheduled jobs, mounting the API app). It is exempt from § Enforcing the Dependency Rule precisely because it is composition, not logic; anything more turns that exemption into a loophole.

---

# Guiding Principle

A folder is not free. Every directory in this tree exists because a prior document — DOC-004, DOC-006, DOC-009, DOC-010, DOC-012, or ADR-006 — already decided it should. If a future change needs a new top-level package, that change belongs first in DOC-009 or DOC-010, and only then here.