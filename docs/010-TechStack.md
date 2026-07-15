---
id: DOC-010
title: Technology Stack & Implementation Policy
version: 2.4
status: Active
owner: CTO
last_updated: 2026-07-13
tags:
  - techstack
  - engineering
  - architecture
  - tdr
  - implementation-policy
related_docs:
  - DOC-004 Architecture
  - DOC-006 Domain Model
  - DOC-007 Data Flow
  - DOC-008 Canonical Glossary
  - DOC-009 System Capabilities
  - DOC-012 Canonical Schema Specification
  - DOC-014 Persistence Policy
  - ADR-006 Blockchain Data Acquisition Strategy
---

# Technology Stack & Implementation Policy

> We don't choose technologies; we make architectural decisions and bind them to technologies.
> Every technology here is a placeholder until proven insufficient, at which point the Migration Trigger defines the path forward.

---

# Purpose

This document defines the concrete technologies used to implement the capabilities defined in DOC-009 — and, beyond a simple inventory, the policy governing each of them: why it was chosen, where it is permitted, where its use is forbidden, when it must be replaced, and how it relates to the Architecture (DOC-004) and to the Architectural Decision Records that justify it.

DOC-010 is the bridge between Architecture and Codebase. A Senior Engineer reading this document should know, for every technology in the stack:

- why it was chosen
- where it is allowed to be used
- where its use is forbidden
- when it must be replaced
- how it relates to the Architecture
- which ADR, if any, governs it

Every technology decision remains documented as a **Technology Decision Record (TDR)**. What changes in this version is the organization: technologies are grouped by their role in the running system rather than by abstract layer name, and each group states its usage policy explicitly.

---

# Design Principles

- No technology in this document is permanent. Every technology is a placeholder, bound to an architectural decision, until proven insufficient.
- Technologies are ephemeral. Decisions are permanent.
- A technology is adopted only after it is bound to a specific architectural decision — never adopted for its own sake.
- Every technology must remain replaceable without changing the Domain Model (DOC-006) or the Canonical Glossary (DOC-008).
- Business logic must never depend on a technology's specific API beyond the abstraction boundary defined for its layer.

---

# Technology Selection Philosophy

Technology is selected according to five priorities, evaluated in order:

1. **Domain Correctness** — A candidate must not violate Point-in-Time Correctness, the Financial Precision Principle, or any rule defined in the Canonical Glossary (DOC-008). No convenience justifies a Domain violation.
2. **Development Velocity** — Among domain-correct options, the platform prefers whichever lets a single engineer ship and iterate fastest (DOC-004; ADR-006, Problem 5).
3. **Deterministic Behavior** — The technology must support reproducible, deterministic pipelines (ADR-006, Principle 3). Non-deterministic behavior disqualifies a technology regardless of its performance.
4. **Modularity** — The technology must respect layer boundaries and must not leak implementation details into the Domain Model.
5. **Replaceability** — The technology must be swappable without rewriting business logic. This generalizes ADR-006 Principle 6 (Provider Independence) to every infrastructure choice, not only RPC providers.

No technology in this project is sacred. If the Domain Model requires a different technology, the technology changes — never the Domain.

---

# Runtime

### Python 3.12

- **Chosen Because:** Stable typing system, mature `asyncio`, and the dominant ecosystem for AI, Quant research, and Web3 tooling (web3.py-class libraries, Pydantic, Polars). Long-term support window.
- **Alternatives Considered:** Go (high-frequency), Rust (performance).
- **Why Rejected:** Go and Rust lack the mature AI/ML/quant ecosystem required by Market Analytics, Research Platform, and future Machine Learning capabilities (DOC-009). Adopting them now would be premature optimization for an MVP.
- **Migration Trigger:** See § Future Evolution — Collector Performance.

---

# Programming Languages

## Primary

**Python** — implements all business logic, all Capabilities, and all Canonical Schemas.

## Secondary

**SQL** — permitted only for TimescaleDB continuous aggregates and analytical queries where SQLAlchemy Core is insufficient. SQL must never be used to bypass Canonical Schema validation on writes.

**YAML** — configuration only (e.g., `confirmation_depth` per chain, defined in ADR-006; Docker Compose definitions). Never used to encode business logic.

**Bash** — developer tooling and operational scripts only. Never part of a production processing pipeline.

## Future

**Go**, **Rust** — reserved for the Collector layer only, and only behind the existing `BlockchainProvider` interface (ADR-006, Provider Abstraction). See § Future Evolution for the trigger condition. No other layer is a candidate for a language migration at this time.

---

# Infrastructure

### Containerization

**Docker + Docker Compose**

- **Chosen Because:** Reproducible local environments; single-command startup for the modular monolith.
- **Deployment Model:** Local-first, single machine. No orchestration platform is required for the MVP.
- **Migration Trigger:** See § Constraints and § Future Evolution — Kubernetes remains explicitly out of scope until Phase 9+ (DOC-005).

### Developer Tooling

**uv**

- **Chosen Because:** Extremely fast Python package manager with deterministic lockfiles and workspace support for the modular monolith.
- **Alternatives Considered:** pip, Poetry.
- **Why Rejected:** pip lacks robust lockfiles; Poetry is slower and less strict.
- **Migration Trigger:** None expected.

### Job Scheduling

**APScheduler**

- **Chosen Because:** Lightweight, in-process job scheduling (e.g., Outcome Generation every 24h).
- **Alternatives Considered:** Celery, raw `asyncio` loops.
- **Why Rejected:** Celery requires a Redis/RabbitMQ broker — overkill for a local MVP. Raw `asyncio` loops are fragile across restarts.
- **Migration Trigger:** If distributed task execution across multiple machines becomes necessary, migrate to Celery or Temporal.

---

# Storage

Storage is the most consequential set of decisions in this document — it is where the platform's central guarantee, reproducible and point-in-time-correct history, either holds or breaks.

## PostgreSQL

Operational storage.

Owns:

- Token
- Trading Pair
- Liquidity Pool
- Wallet
- Smart Contract
- Metadata
- Outcome
- Insight
- Blockchain Facts (append-only; DOC-007, DOC-012 § B.1 — not modified in place, only appended)
- Ingestion Checkpoints (ADR-006 — operational metadata, not analytical data)

### Persistence Access Layer — SQLAlchemy 2.x (Core + ORM)

- **Chosen Because:** Robust, Pythonic database interaction. Core for performance-sensitive paths, ORM for convenience.
- **Alternatives Considered:** Raw SQL, Tortoise ORM.
- **Why Rejected:** Raw SQL is error-prone and unmaintainable at scale. Tortoise is async-only and less mature.
- **Migration Trigger:** None expected.
- **Architectural Constraint:** Business Logic must never see ORM models. ORM models are persistence implementations; Domain Entities communicate exclusively through Canonical Schemas (DOC-008).

## TimescaleDB

Analytical storage. **Only.**

Owns:

- Observation Snapshot
- Market Bar
- Time-series Feature

No Entity is ever stored inside TimescaleDB.

- **Chosen Because:** TimescaleDB is a PostgreSQL extension, providing continuous aggregates and compression for OHLCV/Snapshots without operating a second database engine.
- **Alternatives Considered:** ClickHouse (separate infrastructure), MongoDB (no relational integrity).
- **Why Rejected:** ClickHouse adds operational complexity for a local MVP. MongoDB cannot enforce relational integrity for Domain Entities.
- **Migration Trigger:** See § Future Evolution — Storage Evolution.

## Storage Separation

```text
Operational Data
       ↓
   PostgreSQL

Analytical Data
       ↓
   TimescaleDB

Transient Event Transport
       ↓
   Redis Streams

Current State Cache
       ↓
      Redis
```

These four responsibilities are never merged. Operational entities never live in TimescaleDB. Analytical time-series never live in the PostgreSQL tables designed for entities. Redis holds nothing that cannot be reconstructed by replaying the blockchain (see § Event Transport).

---

# Event Transport

### Redis Streams

- **Chosen Because:** Sub-millisecond latency; native Streams primitive for the Event Transport Layer; doubles as the Current State cache.
- **Alternatives Considered:** Apache Kafka, RabbitMQ.
- **Why Rejected:** Kafka is operationally overkill for a local MVP. RabbitMQ lacks native stream-processing semantics.
- **Migration Trigger:** See § Future Evolution — Event Throughput.

Redis is **not** a database. Redis is **not** a permanent event store. Redis provides:

- Event Transport (Streams)
- Consumer Groups
- Backpressure handling
- Temporary buffering
- Current State Cache

Blockchain history remains replayable. Any message Redis loses before acknowledgement is recoverable through Backfill, exactly as defined in ADR-006 § Failure Recovery — Redis Failure:

> **Redis accelerates the pipeline. Blockchain preserves the truth.**

---

# Blockchain Connectivity

```text
Collector
     │
     ▼
BlockchainProvider (interface)
     │
 ┌───┼───────────────┬───────────┐
 │   │               │           │
 ▼   ▼               ▼           ▼
Alchemy QuickNode  Infura   Ankr / Chainstack
```

Business logic never depends on a provider SDK. The Collector interacts only through the `BlockchainProvider` abstraction defined in ADR-006 § Provider Abstraction. Switching providers must never require a change to domain logic.

### HTTPX

- **Chosen Because:** Modern, async-first HTTP client. Required both for RPC/enrichment calls and for the commodity providers Bought under ADR-006 (GoPlus, DexScreener, GeckoTerminal).
- **Alternatives Considered:** `requests` (sync only, blocks the event loop).
- **Why Rejected:** `requests` blocks async execution; HTTPX supports it natively.
- **Migration Trigger:** None expected for the Python ecosystem.

### WebSockets

- **Chosen Because:** Standard Python WebSocket client for real-time blockchain streams where provider SDKs are insufficient.
- **Alternatives Considered:** Vendor-specific SDK built-in providers.
- **Why Rejected:** Vendor SDKs do not cover all custom WebSocket feeds or the reconnection logic robust Data Acquisition requires.
- **Migration Trigger:** None expected.

### Current Implementation

Python, using a `web3.py`-class client or a raw JSON-RPC/WebSocket client (ADR-006, Alternatives Considered — Option A).

`web3.py` is no longer an architectural decision recorded by this document — it is an implementation detail behind the `BlockchainProvider` interface, and may be changed without a TDR update as long as the interface contract holds.

---

# Data Processing

### Polars

- **Chosen Because:** Columnar, SIMD-optimized, parallel execution; Apache Arrow memory format; streaming capability. The standard for modern quant platforms.
- **Alternatives Considered:** Pandas.
- **Why Rejected:** Pandas is single-threaded, memory-heavy, and slow for large-scale on-chain datasets.
- **Migration Trigger:** None expected.

### NumPy

- **Chosen Because:** Foundational mathematical operations and low-level array manipulation.
- **Alternatives Considered:** None.
- **Migration Trigger:** None.

### Pydantic V2

- **Chosen Because:** Strict schema validation, serialization, and settings management. Implements the Canonical Schemas (DOC-008).
- **Alternatives Considered:** Marshmallow, custom validation.
- **Why Rejected:** Marshmallow is slower; custom validation is unmaintainable.
- **Migration Trigger:** None expected.

### Schema Version Dispatcher

- **Chosen Because:** Canonical Schemas require versioning. A dispatcher routes `schema_version` to the correct parser (V1, V2, …) to prevent breaking changes.
- **Alternatives Considered:** In-place schema mutation.
- **Why Rejected:** Violates reproducibility and breaks downstream consumers.
- **Migration Trigger:** None. This is an architectural pattern, not a library.

---

# API Layer

### FastAPI

- **Chosen Because:** Async-native, automatic OpenAPI documentation, native Pydantic integration. AI-friendly — agents can read the OpenAPI spec directly.
- **Alternatives Considered:** Flask, Django.
- **Why Rejected:** Flask is sync. Django is too heavy for a microservice/API core.
- **Migration Trigger:** None expected.

---

# Research Workspace

### Streamlit

- **Chosen Because:** Rapid prototyping for the Research Workspace; Python-only frontend development.
- **Alternatives Considered:** React, Dash.
- **Why Rejected:** React requires JavaScript expertise the MVP doesn't need yet. Dash has a steeper learning curve for the same outcome.
- **Status:** Temporary MVP UI.
- **Migration Trigger:** When multi-user interactive dashboards or real-time execution UIs are required, replace with React/Next.js.

---

# AI Development Infrastructure

**Not part of the MVP.**

DOC-003 (MVP) explicitly excludes LLM integration, multi-agent orchestration, RAG, and the knowledge graph. DOC-009 lists "AI Platform" under Future Capabilities, Out of MVP Scope. Per DOC-005 (Roadmap), these capabilities are addressed starting at **Phase 6 — AI Research Assistant**, after Phase 4 (Machine Learning Foundation) and Phase 5 (Quantitative Research Engine).

No AI orchestration framework is selected at this time. Vendor-specific frameworks (e.g., LangChain) remain rejected due to volatility and lock-in, carrying forward the position from TechStack v1.1.

Potential technologies to evaluate when this phase begins — listed as future candidates, not commitments:

- LangGraph
- OpenAI Agents SDK
- LlamaIndex
- MCP
- RAG infrastructure

This section exists so the eventual evaluation has a documented starting point, not to pre-select a winner.

---

# Observability

### Logging — structlog

- **Chosen Because:** Structured, JSON-formatted logging. Essential for LLM/AI agents to parse and analyze logs in later phases.
- **Alternatives Considered:** Standard library `logging`.
- **Why Rejected:** Standard logging outputs unstructured text, difficult for machines to parse.
- **Migration Trigger:** None expected.

### Health Checks

Not yet implemented in the MVP. A minimal liveness/readiness endpoint is expected alongside the first FastAPI deployment.

### Metrics

Not yet implemented in the MVP.

### Future

**OpenTelemetry** — candidate for distributed tracing once the modular monolith begins splitting into services (DOC-004, Evolution Strategy).

---

# Testing

### pytest + pytest-asyncio + hypothesis

- **Chosen Because:** Standard testing framework. `hypothesis` provides property-based testing, crucial for Canonical Schema edge cases.
- **Alternatives Considered:** `unittest`.
- **Why Rejected:** `unittest` is verbose and lacks a plugin ecosystem.
- **Migration Trigger:** None expected.

### Code Quality Gates

**Ruff** (lint + format) and **mypy** (static typing) run on every change.

- **Chosen Because:** Ruff is an extremely fast linter/formatter written in Rust, replacing Black + isort + flake8 with one tool. mypy is the standard, highly configurable type checker.
- **Alternatives Considered:** Black + flake8; pyright.
- **Why Rejected:** Black + flake8 requires configuring multiple tools and is slower. mypy remains the more configurable standard.
- **Migration Trigger:** None expected for either.

### Integration Tests

Exercise a full pipeline slice (Collector → Fact Processor → Persistence) against a local Postgres/Redis instance, not mocks.

### Replay Tests

The primary regression test for the platform's central guarantee. A Replay Test re-processes a fixed, known slice of historical blockchain data through the live pipeline and asserts that the resulting Blockchain Facts, Market Bars, Features, and Outcomes match a stored baseline. This directly validates ADR-006 Principle 2 — Reproducibility First.

"Match" is not one rule for every field type. `Decimal`/`String` fields (every raw amount, price, and Token Amount — DOC-008 § Financial Precision) and structural presence/absence of records (which Facts exist, which Bars were produced) are asserted **byte-identical**, with zero tolerance. Native `float` fields (`Feature.value`; `Blockchain.avg_block_time_seconds`) are asserted within a mathematical tolerance (e.g., `assert abs(a - b) < 1e-10`) instead — Polars' multi-threaded aggregation does not guarantee a fixed floating-point accumulation order, so byte-identical equality on a `float` is not a claim this stack can actually make without pinning `POLARS_MAX_THREADS=1` and forfeiting the parallelism Polars was chosen for (§ Data Processing). See DOC-013 § Determinism Discipline for the full reasoning and the rule a reviewer checks a PR against; this is the one-line summary of that rule, not a second, independent definition of it.

### Schema Validation Tests

Property-based tests (via `hypothesis`) against every Canonical Schema, covering version-boundary and malformed-input edge cases before they can reach the Fact Processor.

---

# Security

### Secrets & Configuration

**Pydantic Settings**, loading from a git-ignored local `.env` file — not raw `os.environ` access, and not a second configuration format. This is unchanged from the TDR in TechStack v1.1 (Pydantic Settings over raw `python-dotenv`, for validation and type safety); `.env` remains the local storage convention, but it is always read through Pydantic Settings, never accessed directly.

Policy:

- Secrets (RPC keys, API keys) are never committed to version control.
- Secrets are never hardcoded in source.
- `.env` is listed in `.gitignore` from the first commit.

### Out of Scope for the MVP

The platform is a single-user, local deployment (DOC-003, Non-Goals — wallet authentication is explicitly excluded). This section covers secrets hygiene only. User authentication, authorization, and network-exposed API hardening are not addressed here and should not be assumed to exist until a dedicated ADR introduces them — expected no earlier than the Research Platform becoming multi-user (DOC-009, Capability Maturity).

---

# Capability → Technology Mapping

| Capability (DOC-009) | Primary Technology | Notes |
|---|---|---|
| Data Acquisition | Transport (HTTPX, WebSockets, `BlockchainProvider`), Application (Python) | Real-time listeners; see ADR-006 |
| Data Processing | Validation (Pydantic, Schema Version Dispatcher), Application (Python) | Normalization & Fact Extraction |
| Domain Management | Persistence (SQLAlchemy), Storage (PostgreSQL) | Entity resolution |
| Market Analytics | Analytics (Polars, NumPy), Storage (TimescaleDB) | Projections, Bars, Features |
| Intelligence | Transport (HTTPX — commodity providers), Application (Python) | Rule-based Risk Engine |
| Research Platform | API (FastAPI), Visualization (Streamlit) | Dataset generation & UI |
| Strategy | Application (Python) | Rule-based ranking/filtering |

### Cross-Cutting Concerns

Not DOC-009 Capabilities — infrastructure every Capability above depends on.

| Concern | Technology |
|---|---|
| Scheduling | APScheduler |
| Logging | structlog |
| Configuration & Secrets | Pydantic Settings |
| Testing & Quality | pytest, hypothesis, Ruff, mypy |

**AI Platform** is not mapped to a technology in this table. It is a DOC-009 Future Capability, deferred to DOC-005 Roadmap Phase 6. See § AI Development Infrastructure.

---

# Constraints

The following technologies are explicitly excluded from the current phase of development. They are documented to preserve architectural direction without inviting scope creep.

- **Machine Learning** — `scikit-learn`, `PyTorch` (Deferred to Phase 4, DOC-005).
- **AI Orchestration** — no framework selected; see § AI Development Infrastructure.
- **Execution Engine** — high-frequency execution requires Go/Rust (Phase 8, DOC-005).
- **Kubernetes** — Phase 9+ (DOC-005). Docker Compose only until then.
- **Blockchain Providers must be replaceable.** No business logic may depend on a specific RPC vendor's SDK or response shape (ADR-006, Principle 6).

---

# Future Evolution

Full architectural reasoning for each trigger below is defined in ADR-006 § Future Revisit Conditions. This table is the implementation-facing summary — read ADR-006 before acting on any row.

| Current | Future Alternative | Trigger |
|---|---|---|
| Python Collector | Go / Rust Collector | Profiler proves a sustained bottleneck. The `BlockchainProvider` interface must remain unchanged; no downstream module should require modification. |
| Redis Streams | Apache Kafka / NATS JetStream / Redpanda | Sustained event throughput exceeds single-node Redis capacity (>100k msgs/sec). Migration must preserve replayability, ordering, and idempotency. |
| TimescaleDB | ClickHouse / DuckDB / Apache Iceberg / Delta Lake | Analytical storage requirements exceed TimescaleDB's single-node capacity. Operational entities remain in PostgreSQL regardless. |
| Streamlit | React / Next.js | Multi-user interactive dashboards or real-time execution UIs are required. |
| EVM-only ingestion | Non-EVM support (Solana, Sui, Aptos) | Roadmap expands beyond EVM chains (DOC-005, Phase 9). Provider Interface and Canonical Schemas must be reviewed. |
| APScheduler | Celery / Temporal | Distributed task execution across multiple machines becomes necessary. |

---

# Related ADRs

| ADR | Title | Relevance |
|---|---|---|
| ADR-006 | Blockchain Data Acquisition Strategy (Build vs Buy) | Governs § Storage, § Event Transport, § Blockchain Connectivity, and most of § Future Evolution. |

This document currently references one accepted ADR. Additional ADRs will be appended here as they are ratified.

---

# Guiding Principles

> Technology is an implementation detail.
> Architecture protects the domain.
> The domain protects reproducibility.
> Reproducibility protects research.
> Every technology in this document exists to preserve that chain.