---
id: ADR-006
title: Blockchain Data Acquisition Strategy (Build vs Buy)
version: 3.1
status: Accepted
owner: CTO
last_updated: 2026-07-11
tags:
  - adr
  - architecture
  - blockchain
  - ingestion
  - data-acquisition
  - build-vs-buy
  - evm
  - finality
  - reproducibility
related_docs:
  - DOC-004 Architecture
  - DOC-006 Domain Model
  - DOC-007 Data Flow
  - DOC-008 Canonical Glossary
  - DOC-009 System Capabilities
  - DOC-010 Technology Stack
---

# ADR-006 — Blockchain Data Acquisition Strategy (Build vs Buy)

> **Decision Statement**
>
> The platform must own the canonical transformation from raw blockchain activity into reproducible quantitative research artifacts.
>
> External providers may accelerate data acquisition and enrich domain knowledge, but they must never become the authoritative source of blockchain history.

---

# Purpose

This ADR defines how the platform acquires blockchain data, handles blockchain finality, detects chain reorganizations, performs historical replay, and balances **Build vs Buy** decisions.

This document establishes the architectural boundary between:

- infrastructure we fully control,
- infrastructure we partially trust,
- infrastructure we intentionally outsource.

The decisions recorded here directly affect every downstream capability including:

- Blockchain Facts
- State Projection
- Market Analytics
- Feature Engineering
- Outcome Generation
- Quantitative Research
- Future Machine Learning

---

# Background

The entire platform is built on one fundamental assumption:

> Historical blockchain data must always be reproducible.

Everything else depends on this assumption.

If blockchain history becomes inconsistent...

- Features become inconsistent.
- Labels become inconsistent.
- Backtests become invalid.
- Research loses credibility.
- ML datasets become poisoned.

Unlike traditional market APIs, blockchain history is not immediately final.

Blocks may be reorganized.

Transactions may temporarily disappear.

RPC providers may disagree for short periods.

External indexing providers may silently change indexing logic.

For a Quant Research Platform, these realities are unacceptable unless explicitly modeled.

---

# Problem Statement

The platform must solve five engineering problems simultaneously.

## Problem 1 — Blockchain Reorganizations

A blockchain block is probabilistic until sufficient confirmations have accumulated.

A transaction observed today may disappear after a chain reorganization.

If this transaction is immediately accepted as immutable historical truth, every downstream artifact becomes corrupted.

---

## Problem 2 — Deterministic Replay

Researchers must be able to reconstruct historical datasets exactly as they existed.

Running the same replay twice over identical blockchain history must produce identical:

- Blockchain Facts
- Market Bars
- Features
- Outcomes

Non-deterministic pipelines are unacceptable.

---

## Problem 3 — Low-Latency New Pair Discovery

The platform monitors newly deployed liquidity pools.

Latency matters.

Waiting for external indexers introduces unnecessary delay.

The platform must discover new pairs as close to on-chain creation as practical.

---

## Problem 4 — Vendor Independence

The platform should never depend on proprietary indexing logic.

Changing an RPC provider must never require changing business logic.

Replacing infrastructure must not change historical truth.

---

## Problem 5 — Long-Term Maintainability

The MVP is developed by a single engineer.

The architecture must remain:

- understandable,
- deterministic,
- modular,
- evolvable.

The solution must avoid unnecessary operational complexity while preserving future scalability.

---

# Architectural Principles

The following principles are mandatory.

---

## Principle 1 — Source of Truth Ownership

The platform owns the canonical transformation from blockchain history into research data.

The authoritative history consists only of:

- blockchain events,
- acquired directly from blockchain RPC interfaces,
- transformed exclusively by our Fact Processing Pipeline,
- persisted as Blockchain Facts defined in DOC-006.

Nothing else is considered authoritative.

---

## Principle 2 — Reproducibility First

Every transformation must be reproducible.

Given:

- identical blockchain history
- identical schemas
- identical transformation rules

the platform must always generate identical outputs.

This applies to every downstream artifact.

---

## Principle 3 — Deterministic Pipelines

Every processing step must behave deterministically.

Randomness, hidden state, mutable external dependencies, and provider-specific behavior must never influence canonical outputs.

---

## Principle 4 — Build the Intellectual Property

We build systems that represent the platform's competitive advantage.

Examples include:

- Fact Extraction
- Finality Management
- State Projection
- Feature Engineering
- Outcome Generation

These systems define research correctness.

They are never outsourced.

---

## Principle 5 — Buy Commodities

Commodity capabilities should be purchased instead of rebuilt.

Examples include:

- metadata
- token logos
- security scoring
- verified source code
- wallet labels
- social metrics

These accelerate development but do not define historical truth.

---

## Principle 6 — Provider Independence

RPC providers are infrastructure.

They are interchangeable.

Business logic must never depend on:

- Alchemy
- QuickNode
- Infura
- Chainstack
- Ankr

The Collector interacts only through an abstract provider interface.

---

## Principle 7 — Canonical Schemas Everywhere

Every internal module communicates only through Canonical Schemas.

No module exchanges provider-specific payloads.

Normalization happens once.

Only canonical representations move through the platform.

---

## Principle 8 — Replay Before Performance

The ability to replay history correctly is more valuable than processing it faster.

Performance optimizations must never compromise reproducibility.

---

# Build vs Buy Philosophy

The guiding question is never:

> Can we build it?

The real question is:

> Does building this capability create long-term competitive advantage?

If the answer is **yes**, we build it.

If the answer is **no**, we buy it.

---

# Decision

After evaluating multiple approaches, the platform adopts the following strategy:

> **Build the Source of Truth. Buy the Enrichment.**

Specifically,

the platform will:

- acquire blockchain events directly from EVM RPC providers,
- normalize all blockchain events internally,
- manage blockchain finality internally,
- detect chain reorganizations internally,
- generate Blockchain Facts internally,
- replay historical data internally.

Conversely,

the platform may consume external services for:

- metadata,
- wallet intelligence,
- security analysis,
- contract verification,
- social information,
- ecosystem enrichment.

External services improve productivity.

They never define historical truth.

---

# Build vs Buy Matrix

| Capability | Strategy | Reason |
|------------|----------|--------|
| Blockchain Event Acquisition | **Build** | Foundation of historical truth |
| Event Normalization | **Build** | Core domain logic |
| Blockchain Fact Generation | **Build** | Canonical research data |
| Reorg Detection | **Build** | Critical for correctness |
| Finality Management | **Build** | Required for deterministic history |
| Replay Engine | **Build** | Required for reproducibility |
| Checkpoint Management | **Build** | Required for recovery |
| Market Bar Generation | **Build** | Research intellectual property |
| Feature Engineering | **Build** | Research intellectual property |
| Outcome Generation | **Build** | ML dataset integrity |
| Token Metadata | **Buy** | Commodity information |
| Contract Verification | **Buy** | Commodity information |
| Wallet Labels | **Buy** | Commodity enrichment |
| Security Scoring | **Buy** | Commodity enrichment |
| Social Metrics | **Buy** | Commodity enrichment |
| Token Logos | **Buy** | UI enrichment |

---

# Alternatives Considered

Before selecting the final architecture, multiple approaches were evaluated.

---

## Option A — Raw JSON-RPC / WebSocket Client (Pure Build)

### Description

Implement the complete acquisition pipeline directly on top of JSON-RPC and WebSockets.

Implementation technology is intentionally unspecified. Concrete libraries are selected in DOC-010 Technology Stack.

Responsibilities include:

- block subscriptions
- log subscriptions
- normalization
- reorg detection
- checkpointing
- replay
- backfill

### Advantages

- Maximum control
- No vendor lock-in
- Fully reproducible
- Canonical history remains under our ownership
- Perfect alignment with the platform architecture

### Disadvantages

- Highest implementation effort
- Requires implementing finality logic
- Requires implementing replay
- Requires implementing checkpoint recovery

---

## Option B — Managed Blockchain Indexers

Examples:

- Goldsky
- Alchemy Streams
- QuickNode Streams

### Advantages

- Fast implementation
- Minimal infrastructure
- Managed scaling
- Managed indexing

### Disadvantages

- Vendor lock-in
- Provider-specific schemas
- Opaque indexing logic
- Unknown reorg implementation
- Historical behavior may silently change

### Decision

Rejected as the primary data source.

May be used only as optional enrichment.

---

## Option C — The Graph / Subgraphs

### Advantages

- Powerful GraphQL interface
- Mature ecosystem
- Historical querying
- Managed indexing

### Disadvantages

- Indexing latency
- Dynamic contract lag
- Limited suitability for immediate new pair detection
- External indexing logic

### Decision

Rejected for primary ingestion.

Potentially useful for secondary research queries.

---

## Option D — web3-ethereum-defi

### Advantages

- High-level DeFi abstractions
- Historical utilities
- Useful analytics tooling

### Disadvantages

- Strong Pandas coupling
- Opinionated internal abstractions
- Less control over canonical transformation pipeline

### Decision

Rejected for the core ingestion layer.

May be evaluated later for research tooling.

---

## Option E — Ethereum ETL / Firehose / StreamingFast

### Advantages

- Excellent historical extraction
- Massive throughput
- Enterprise-grade analytics

### Disadvantages

- Significant operational complexity
- Infrastructure overhead
- Overkill for a local MVP

### Decision

Deferred to future scaling phases.

---

## Option F — Archive Nodes / Trace APIs

Examples include:

- Erigon
- Reth
- Trace APIs

### Advantages

- Deep historical visibility
- Internal transaction tracing
- Advanced wallet analysis

### Disadvantages

- Expensive
- Heavy infrastructure
- Unnecessary for MVP

### Decision

Deferred until advanced wallet intelligence becomes a priority.

---

# Final Decision Summary

The platform adopts a **Hybrid Build Strategy**.

**Build**

- Blockchain acquisition
- Fact extraction
- Finality
- Replay
- Checkpointing
- Canonical schemas
- Market analytics

**Buy**

- Metadata
- Security intelligence
- Wallet labels
- Contract verification
- Ecosystem enrichment

This boundary preserves the platform's intellectual property while maximizing development velocity.

### Architecture

The architecture separates **blockchain connectivity** from **domain logic**.

Everything below the `Blockchain Provider Interface` is considered **our platform** and therefore deterministic, testable, and reproducible.

```text
                    ┌─────────────────────────────┐
                    │   Blockchain Providers      │
                    │─────────────────────────────│
                    │ Alchemy                     │
                    │ QuickNode                   │
                    │ Infura                      │
                    │ Chainstack                  │
                    │ Local Node                  │
                    └─────────────┬───────────────┘
                                  │
                                  ▼
                  Blockchain Provider Interface
                                  │
                                  ▼
                        Block Collector
                                  │
                    Raw Blockchain Events
                                  │
                                  ▼
                         Redis Streams
                                  │
                                  ▼
                        Fact Processor
                                  │
                                  ▼
                     Blockchain Facts
                     (Pending Status)
                                  │
                                  ▼
                      Finality Buffer Engine
                      │                  │
                      │                  │
                 Finalized          Orphaned
                      │
                      ▼
               Historical Fact Store
                      │
        ┌─────────────┴─────────────┐
        │                           │
        ▼                           ▼
 Market Analytics Pipeline    State Pipeline
        │                           │
        ▼                           ▼
  Market Bars (OHLCV)      Observation Snapshots
        │                           │
        └─────────────┬─────────────┘
                      ▼
             Feature Engineering
                      │
                      ▼
                 Research Layer
```

---

# Provider Abstraction

The Collector must never depend on a specific RPC vendor.

All providers must implement a common interface.

Changing infrastructure providers must not require changes to domain logic.

```text
Collector
     │
     ▼
BlockchainProvider
     │
 ┌───┼───────────────┐
 │   │               │
 ▼   ▼               ▼
Alchemy QuickNode  Local Node
```

The interface is responsible for exposing only blockchain primitives.

Typical responsibilities include:

* Subscribe to new blocks
* Subscribe to contract events
* Retrieve historical logs
* Retrieve block metadata
* Retrieve transaction receipts

Business concepts such as:

* PairCreated
* SwapExecuted
* Liquidity Added
* Rug Pull

must **never** exist inside the provider implementation.

Those belong to the **Fact Processor**.

This separation guarantees:

* Provider independence
* Easier testing
* Easier migration
* Stable domain logic

RPC providers are transport mechanisms, not trusted data sources. Different providers may expose temporary inconsistencies due to propagation delays, indexing strategies, or infrastructure issues. Therefore every provider output must pass through the Canonical Normalization layer before entering the Historical Fact Pipeline.

---

# Finality & Canonical Chain Validation Engine

## Purpose

Blockchains are probabilistic.

Receiving a block does **not** mean the block is final.

A temporary chain fork may invalidate previously observed events.

Therefore, Blockchain Facts cannot immediately become historical truth.

The Finality & Canonical Chain Validation Engine is responsible for converting probabilistic blockchain events into deterministic historical facts.

---

## Confirmation Lifecycle

Every Blockchain Fact follows the same lifecycle.

```text
Observed

↓

Pending

↓

Confirmed

↓

Finalized

or

Orphaned
```

### Pending

The event has been received.

No confirmation has been observed yet.

The Fact is stored but must not be used for analytical pipelines.

---

### Confirmed

Additional blocks have been mined.

The probability of rollback decreases.

Still not considered immutable.

---

### Finalized

Required confirmation depth has been reached.

The Fact becomes immutable historical truth.

Only Finalized Facts may participate in:

* Market Bar generation
* Feature Engineering
* Backtesting
* Dataset generation
* Research

---

### Orphaned

A chain reorganization invalidated the originating block.

The Fact remains stored for auditability but is excluded from downstream processing.

Facts are never deleted.

Only their confirmation status changes.

---

## Configurable Confirmation Depth

Confirmation depth is chain-specific.

The platform must not hardcode confirmation rules.

Example configuration:

```yaml
confirmation_depth:

  ethereum: 3

  base: 3

  bnb: 5
```

Future chains may define different thresholds without changing application logic.

---

## Why Store Pending Facts?

Persisting Pending Facts provides several advantages:

* Ultra-low latency dashboards
* Complete audit trail
* Accurate latency measurement
* Easier debugging
* Full replay capability

Analytical pipelines simply ignore non-finalized facts.

---

# Canonical Chain Validation Engine

The Finality & Canonical Chain Validation Engine does not validate continuity by comparing only the parent hash of the most recently received block.

Instead, it maintains an in-memory buffer of the **last N block headers**, where N is the configured confirmation depth for that chain (see Configurable Confirmation Depth above). On every new block, the engine verifies the continuity of the entire canonical chain across that confirmation window — not a single block.

Conceptually:

```text
New Block Arrives

↓

Engine Verifies Canonical Chain Continuity

↓

Across the Configured Confirmation Depth (last N headers)

↓

Continuity Holds?

│

├── Yes

│      ▼

│ Continue — Advance Confirmation Status

│

└── No

       ▼

Chain Reorganization Detected

↓

All Affected Facts Marked Orphaned

↓

Replay Canonical Chain
```

This is a **confirmation window**, not a single-block comparison. Checking only the immediate parent hash is insufficient for EVM networks, where reorganizations can extend multiple blocks deep during periods of network instability. Validating continuity across the full confirmation window allows the engine to detect and correctly resolve multi-block reorganizations, not only single-block reorgs.

Reorganization handling is deterministic.

The platform always converges toward canonical blockchain history.

---

# Data Flow

The acquisition pipeline is deterministic. From Historical Fact Store onward it branches into two independent downstream paths — Market Analytics and State Projection — consistent with DOC-007 Data Flow.

```text
RPC / WebSocket

↓

Blockchain Provider

↓

Collector

↓

Raw Blockchain Event

↓

Redis Stream

↓

Fact Processor

↓

Blockchain Fact (Pending)

↓

Finality & Canonical Chain Validation Engine

↓

Blockchain Fact (Finalized)

↓

Historical Fact Store

        │

        ├────────► Projection Engine

        │               │

        │               ▼

        │        Observation Snapshots

        │

        ▼

Market Analytics

        │

        ▼

Market Bars (OHLCV)

        │

        └───────┬───────┘

                ▼

       Feature Engineering

                ↓

            Research
```

Every stage has a single responsibility.

| Stage                              | Responsibility                                                        |
| ----------------------------------- | ---------------------------------------------------------------------- |
| Blockchain Provider                 | Retrieve raw blockchain data from external infrastructure              |
| Collector                           | Acquire blocks and logs with minimal transformation                    |
| Redis Streams                       | Decouple ingestion from processing and provide buffering               |
| Fact Processor                      | Normalize raw events into Canonical Blockchain Facts                   |
| Finality & Canonical Chain Validation Engine | Handle confirmations, detect reorgs (single- and multi-block), determine canonical history |
| Historical Fact Store               | Persist immutable finalized facts                                      |
| Projection Engine                   | Derive current State from Facts and record Observation Snapshots       |
| Market Analytics                    | Produce deterministic Market Bars from finalized Swap Facts            |
| Feature Engineering                 | Compute quantitative features from Observation Snapshots and Market Bars, without lookahead bias |
| Research Layer                      | Generate datasets, analyses, and reproducible research artifacts       |

---

# Architectural Guarantees

This architecture guarantees the following properties:

* **Provider Independence** — Switching RPC providers requires no domain changes.
* **Deterministic Processing** — Every event follows exactly one processing path.
* **Replayability** — Historical data is processed identically to live data.
* **Reproducibility** — The same inputs always produce the same outputs.
* **Reorg Safety** — Blockchain reorganizations cannot silently corrupt historical facts.
* **Point-in-Time Correctness** — Downstream analytics only consume finalized information available at that historical moment.
* **Separation of Concerns** — Infrastructure, acquisition, normalization, finality, and analytics remain independently evolvable.

---

> **Architectural Principle**
>
> **The blockchain is the source of events.**
>
> **The platform is the source of truth.**


---

# Checkpointing

## Why Checkpointing Exists

The blockchain is an append-only ledger, but our platform is a long-running streaming system.

Unexpected failures can occur at any time:

- Process crash
- Power outage
- Docker restart
- Network interruption
- RPC provider outage

The platform must always be able to resume ingestion without:

- Missing blocks
- Duplicating Facts
- Breaking chronological ordering

Checkpointing defines where the platform can safely continue processing.

---

## Checkpoint Strategy

Only **Finalized** blocks are eligible for checkpointing.

Persisting checkpoints for Pending or Confirmed blocks risks replay inconsistencies during blockchain reorganizations.

Each supported chain maintains an independent checkpoint.

Example:

```text
Ethereum  → 21,350,445
Base      → 18,992,114
BNB Chain → 46,501,823
```

---

## Stored Checkpoint

A checkpoint represents the highest finalized block that has been completely processed.

Example schema:

```yaml
chain: ethereum

last_finalized_block: 21350445

updated_at: 2026-07-10T15:42:17Z
```

The checkpoint is persisted in PostgreSQL because it is operational metadata rather than analytical data.

---

## Recovery Procedure

After startup:

```text
Load checkpoint

↓

Connect to RPC

↓

Read current chain head

↓

Determine missing block range

↓

Replay missing blocks

↓

Resume live streaming
```

Example:

```text
Checkpoint:
21,350,445

Current Head:
21,350,478

Replay:

21,350,446

↓

...

↓

21,350,478

↓

Switch to Live Mode
```

The Collector never resumes directly from the current head.

It always fills historical gaps first.

---

# Replay

## Purpose

Replay is one of the fundamental capabilities of the platform.

It guarantees that historical research can always be reproduced.

Replay means processing historical blockchain data through the exact same pipeline used for live data.

No alternative execution path is permitted.

---

## Replay Pipeline

```text
Historical Blocks

↓

Collector

↓

Redis Stream

↓

Normalizer

↓

Fact Processor

↓

Persistence

↓

Projection Engine

↓

Market Analytics

↓

Feature Engineering
```

Every module receives replayed events exactly as if they had arrived in real time.

The only difference is the event source.

---

## Single Processing Path

Historical data and live data must never have separate implementations.

Incorrect:

```text
Live

↓

Pipeline A

Historical

↓

Pipeline B
```

Correct:

```text
Live Events

↓

Shared Pipeline

Historical Events

↓

Shared Pipeline
```

Maintaining a single execution path eliminates behavioral drift between historical research and production ingestion.

---

## Reproducibility

Replay must satisfy the following invariant:

> Given the same blockchain history, the platform must always produce identical Facts, State Projections, Market Bars, Features, and Outcomes.

This property is essential for:

- Backtesting
- Quantitative research
- AI training datasets
- Debugging
- Regression testing

---

# Idempotency

## Principle

Every processing stage must be idempotent.

Processing the same event multiple times must produce the same final system state.

Duplicate processing must never create duplicate facts.

---

## Why It Matters

Duplicate events may occur because of:

- RPC retries
- Redis redelivery
- Process restart
- Manual replay
- Network timeout

The platform must treat duplicates as expected behavior.

---

## Canonical Event Identity

Every Blockchain Fact has a deterministic identity.

Example:

```text
chain
+
block_number
+
transaction_hash
+
log_index
```

This composite identifier uniquely identifies an EVM log.

No surrogate UUIDs are used for Facts.

---

## Persistence Rules

Insertion must be idempotent.

Examples:

- UPSERT
- INSERT ... ON CONFLICT DO NOTHING
- INSERT ... ON CONFLICT UPDATE

Duplicate blockchain events are ignored or merged rather than inserted twice.

---

## Downstream Processing

Derived objects must also be deterministic.

Examples:

- State Projection
- Market Bars
- Features

Re-running aggregation over the same finalized Facts must always produce identical outputs.

---

# Failure Recovery

## Design Philosophy

Failures are normal.

Recovery must be automatic.

No manual intervention should be required after ordinary failures.

---

## Expected Failure Types

The platform explicitly supports recovery from:

- Collector crash
- Processor crash
- Redis restart
- PostgreSQL restart
- RPC disconnect
- Docker restart
- Machine reboot
- Temporary provider outage

---

## Recovery Flow

```text
Failure

↓

Restart Service

↓

Load Checkpoint

↓

Replay Missing Blocks

↓

Resume Streaming

↓

Continue Processing
```

No historical information should be lost.

---

## Redis Failure

Redis Streams are used only as the transport layer.

If Redis loses in-flight events before acknowledgement:

1. Reload checkpoint.
2. Replay missing finalized blocks from the blockchain.
3. Repopulate the stream.

Because blockchain history is replayable, Redis is never treated as the permanent source of truth.

> **Redis accelerates the pipeline. Blockchain preserves the truth.**

---

## Database Failure

If PostgreSQL or TimescaleDB becomes unavailable:

- Event ingestion pauses.
- Collectors may continue buffering temporarily (subject to configured limits).
- Once storage recovers, replay guarantees eventual consistency.

No data repair scripts should be required for normal outages.

---

## Provider Failure

RPC providers are considered replaceable infrastructure.

Collectors communicate exclusively through the `BlockchainProvider` abstraction.

Example:

```text
Alchemy unavailable

↓

Switch Provider

↓

QuickNode

↓

Continue Processing
```

Business logic remains unchanged.

---

## Final Recovery Guarantee

The platform guarantees eventual consistency after recoverable failures.

Provided that blockchain history remains accessible through an RPC endpoint, the platform can always reconstruct its internal state from finalized blocks.

No internal queue, cache, or transient storage is considered irreplaceable.

---

# Consequences

## Advantages

### Deterministic Research

Every dataset can be regenerated from the blockchain.

Research becomes reproducible by design rather than by convention.

---

### Operational Simplicity

The architecture avoids complex distributed consensus.

A modular monolith with replay capability is sufficient for the MVP while remaining compatible with future scaling.

---

### Strong Data Integrity

Finality buffering, checkpointing, and idempotent persistence ensure that temporary failures do not corrupt historical truth.

Facts are never silently modified.

Instead, lifecycle transitions (`Pending → Confirmed → Finalized` or `Orphaned`) preserve an auditable history.

---

### Minimal Vendor Lock-in

Only standard JSON-RPC and WebSocket interfaces are assumed.

RPC providers can be replaced without changing the domain model, canonical schemas, or processing pipeline.

---

### Future Scalability

The ingestion architecture is intentionally modular.

If profiling later identifies throughput bottlenecks, components such as the Collector or Event Transport Layer may be replaced independently (e.g., Go collectors or Kafka) without altering the canonical domain model.

---

### Multi-Block Reorganization Support

The Canonical Chain Validation Engine verifies continuity across the full confirmation window rather than a single block, so it correctly detects and resolves multi-block reorganizations, not only single-block reorgs.

---

## Disadvantages

- Initial implementation is more complex than consuming managed indexers.
- Reorg handling must be implemented and maintained internally.
- Replay logic introduces additional engineering effort.
- High-quality RPC providers remain operational dependencies.
- Requires maintaining an in-memory block header buffer for the configured confirmation depth.

These costs are accepted because they preserve the platform's most important architectural property:

> **The platform owns the canonical transformation from blockchain history to quantitative knowledge.**

This ownership guarantees reproducibility, explainability, and long-term architectural independence. The full discussion of these trade-offs follows below.

# Trade-offs

Every architectural decision introduces constraints.

The chosen strategy intentionally prioritizes long-term research integrity over short-term implementation speed.

---

## Accepted Trade-offs

### Higher Initial Engineering Cost

Building a provider abstraction, Finality Buffer, replay pipeline, and checkpointing system requires more effort than integrating a managed indexing service.

This additional complexity is accepted because these components become permanent platform capabilities rather than external dependencies.

---

### More Infrastructure Responsibility

The platform becomes responsible for:

- detecting blockchain reorganizations
- managing confirmation lifecycles
- replaying historical data
- maintaining checkpoints
- ensuring idempotent processing

These responsibilities would otherwise be delegated to third-party providers.

---

### Dependence on RPC Quality

Although business logic is provider-independent, reliable ingestion still depends on high-quality RPC endpoints.

Poor RPC providers may introduce:

- delayed block propagation
- dropped WebSocket connections
- inconsistent historical responses
- aggressive rate limiting

For this reason, RPC providers are treated as replaceable infrastructure rather than trusted data sources.

---

### Slower MVP Than "Buy Everything"

Using managed indexing platforms would produce a working prototype more quickly.

However, that speed would come at the expense of:

- reproducibility
- explainability
- deterministic replay
- architectural ownership

For a quantitative research platform, those compromises are unacceptable.

---

### Operational Complexity

Replay, checkpointing, and finality introduce additional operational concepts.

This complexity is intentional.

The platform optimizes for correctness before convenience.

---

### Complexity vs. Determinism

```text
Higher implementation complexity

↓

Stronger deterministic guarantees
```

---

### Latency vs. Correctness

```text
Slightly higher latency

↓

Correct historical truth
```

---

# Future Revisit Conditions

Architectural decisions are not permanent.

They should only change when objective evidence demonstrates that a different approach provides measurable value.

The following conditions may trigger a future architectural review.

---

## Event Throughput

If sustained event throughput exceeds the processing capacity of Redis Streams on a single machine, evaluate replacing the Event Transport Layer with:

- Apache Kafka
- NATS JetStream
- Redpanda

Migration must preserve:

- replayability
- ordering guarantees
- idempotent processing

---

## Collector Performance

If profiling shows that Python collectors cannot ingest blockchain events fast enough, evaluate rewriting only the Collection Layer using:

- Go
- Rust

The Provider Interface must remain unchanged.

No downstream module should require modification.

---

## Historical Backfill Scale

If historical replay expands from millions to billions of blockchain events, evaluate specialized ingestion systems such as:

- StreamingFast Firehose
- Ethereum ETL
- Erigon
- Reth

These tools may accelerate large-scale backfills but must continue emitting the platform's Canonical Schemas.

---

## Multi-Chain Expansion

The MVP targets EVM-compatible chains.

If future support extends to non-EVM ecosystems (e.g., Solana, Sui, Aptos), the Provider Interface and Canonical Schemas must be reviewed to accommodate different execution models while preserving the platform's semantic principles.

---

## External Data Providers

New enrichment providers may be integrated if they satisfy all of the following conditions:

- improve research quality
- reduce engineering effort
- do not become the source of truth
- do not replace canonical blockchain history

External services remain optional enrichment layers.

---

## Storage Evolution

If analytical storage requirements exceed the capabilities of TimescaleDB, evaluate dedicated analytical databases such as:

- ClickHouse
- DuckDB (offline research)
- Apache Iceberg
- Delta Lake

Operational entities must remain separate from analytical storage regardless of future technology choices.

---

## AI Platform

Knowledge management, Retrieval-Augmented Generation (RAG), Agent Orchestration, and autonomous development workflows are intentionally excluded from the MVP.

These capabilities will be revisited after the platform satisfies all MVP success criteria defined in `DOC-003`.

---

# References

This Architectural Decision Record should be interpreted together with the following documents.

| Document | Purpose |
|----------|---------|
| DOC-001 Vision | Defines the long-term mission of the platform. |
| DOC-003 MVP | Defines current implementation boundaries and non-goals. |
| DOC-004 Architecture | Describes the high-level system architecture. |
| DOC-006 Domain Model | Defines canonical domain entities and lifecycles. |
| DOC-007 Data Flow | Defines processing pipelines and data movement. |
| DOC-008 Glossary | Defines canonical terminology used throughout the project. |
| DOC-009 System Capabilities | Defines functional capabilities and system responsibilities. |
| DOC-010 Technology Stack | Defines concrete implementation technologies. |

This ADR supplements these documents by explaining **why** the blockchain acquisition architecture was chosen.

---

## External References

- Ethereum Yellow Paper
- Ethereum Execution Layer Specification
- EIP-1898 (Block Parameter Support)
- web3.py Documentation
- Redis Streams Documentation

---

# Final Guiding Principles

The following principles are non-negotiable.

They should guide every future implementation decision.

---

## Own the Source of Truth

The platform owns the canonical transformation from blockchain history to research data.

Third-party services may enrich the platform.

They must never define historical truth.

---

## Determinism Over Convenience

Every pipeline should produce identical outputs when given identical blockchain history.

Reproducibility is a first-class architectural requirement.

---

## Replay Everything

Any historical period must be replayable through the exact same processing pipeline used for live data.

Separate historical code paths are prohibited.

---

## Finality Before Analytics

Analytical pipelines operate only on finalized blockchain history.

Low-latency views may expose Pending or Confirmed data, but quantitative research must be based on Finalized Facts.

---

## Build the Moat, Buy the Commodity

Engineering effort should focus exclusively on capabilities that differentiate the platform.

Commodity capabilities—such as metadata enrichment, security scoring, and contract verification—should be integrated from external providers whenever practical.

---

## Provider Independence

Business logic must never depend on a specific RPC provider.

Infrastructure should remain replaceable without modifying domain behavior.

---

## Architecture Before Optimization

Technology choices should follow measured bottlenecks rather than assumptions.

Premature optimization introduces unnecessary complexity.

Profile first.

Optimize second.

---

## Simplicity Wins

The MVP is intentionally implemented as a modular monolith.

Distributed systems, microservices, and large-scale infrastructure are deferred until objective evidence justifies their complexity.

---

## The Guiding Principle

> **The platform exists to transform raw blockchain activity into reproducible quantitative knowledge.**

Every architectural decision, technology choice, and implementation detail should be evaluated against this objective.

If a change improves reproducibility, correctness, and research quality, it moves the platform forward.

If it does not, it should be rejected.