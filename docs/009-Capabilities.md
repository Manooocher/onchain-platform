---
id: DOC-009
title: System Capabilities
version: 2.1
status: Active
owner: CTO
last_updated: 2026-07-12
tags:
  - capabilities
  - architecture
  - domain
  - business
---

# System Capabilities

> This document defines **what the platform is capable of doing**, not **how it is implemented**.
>
> Capabilities represent stable business responsibilities.
> Technologies, frameworks, and implementation details are intentionally excluded.
>
> This document serves as the bridge between:
>
> - DOC-001 Vision
> - DOC-003 MVP
> - DOC-004 Architecture
> - DOC-006 Domain Model
> - DOC-007 Data Flow
> - DOC-010 Technology Stack

---

# Purpose

The platform is organized around a small number of stable capabilities.

Each capability owns a well-defined responsibility and collaborates with other capabilities through canonical data contracts.

Capabilities are long-lived.

Implementations evolve.

Technologies change.

Capabilities remain.

---

# Design Principles

Every capability must satisfy the following principles.

- Single Responsibility
- Deterministic Behavior
- Reproducible Results
- Point-in-Time Correctness
- Canonical Data Contracts
- Framework Independence
- Explainability
- Modular Evolution

No capability may depend directly on implementation technologies.

---

# MVP Capability Map

The MVP consists of seven core capabilities.

```
Data Acquisition
        │
        ▼
Data Processing
        │
        ▼
Domain Management
        │
        ├─────────────► Market Analytics
        │                     │
        │                     ▼
        │              Intelligence
        │                     │
        └─────────────────────┘
                      │
                      ▼
              Research Platform
                      │
                      ▼
          Strategy (Candidate Ranking)
```

---

# Capability Overview

| Capability | Primary Responsibility |
|------------|-----------------------|
| Data Acquisition | Collect raw blockchain and external data |
| Data Processing | Transform Events into canonical Facts |
| Domain Management | Maintain canonical domain entities |
| Market Analytics | Produce State Projections, Market Bars and Features |
| Intelligence | Evaluate risk and enrich research context |
| Research Platform | Provide datasets, APIs and visualization |
| Strategy | Rank research candidates using deterministic rules |

**This table is the canonical list of the seven MVP Capabilities.** Every other document that maps technology, directories, or anything else onto "the Capabilities" — DOC-010, DOC-011, and any future document — must reference this table, not restate or re-derive its own copy. A capability list that drifts out of sync here has, twice already, quietly picked up an eighth row that wasn't a Capability at all ("Platform Services," "AI Platform"). Cross-cutting infrastructure and deferred Future Capabilities belong in their own clearly-labeled section, never blended into this table.

---

# Capability Definitions

---

# 1. Data Acquisition

## Responsibility

Acquire raw information from external systems.

Sources include:

- Blockchain nodes
- RPC providers
- WebSocket subscriptions
- External APIs
- Security providers

Produces:

- Raw Events

Consumes:

- External Systems

Does NOT:

- Parse blockchain semantics
- Calculate indicators
- Store business entities

---

# 2. Data Processing

## Responsibility

Transform Raw Events into immutable Blockchain Facts.

Responsibilities include:

- Event normalization
- Schema validation
- Canonical ID generation
- Fact extraction
- Event deduplication
- Reorganization handling
- Confirmation tracking

Produces:

- Blockchain Facts

Consumes:

- Raw Events

This capability is the only producer of historical Facts.

---

# 3. Domain Management

## Responsibility

Maintain the canonical representation of business entities.

Owns:

- Tokens
- Trading Pairs
- Liquidity Pools
- Smart Contracts
- Wallets
- Metadata
- Outcomes

Responsibilities:

- Entity lifecycle
- Metadata enrichment
- Identity resolution
- Relationship management

Produces:

- Domain Entities

---

# 4. Market Analytics

## Responsibility

Transform historical Facts into analytical datasets.

Produces:

- State Projections
- Observation Snapshots
- Market Bars (OHLCV)
- Derived Features

Responsibilities:

- State reconstruction
- Time-series aggregation
- Feature engineering
- Temporal consistency

This capability never modifies historical Facts.

---

# 5. Intelligence

## Responsibility

Generate deterministic research signals.

Responsibilities include:

- Rule-based risk analysis
- Scam heuristics
- Honeypot detection
- Liquidity analysis
- Ownership concentration
- External security enrichment

This capability enriches research.

It never executes trades.

Machine Learning is outside the MVP scope.

---

# 6. Research Platform

## Responsibility

Provide a workspace for quantitative research.

Responsibilities include:

- Dataset generation
- Historical replay
- Feature inspection
- API exposure
- Visualization
- Research reproducibility

Produces:

- Research datasets
- Analytical views

This capability is intended for human researchers.

---

# 7. Strategy

## Responsibility

Prioritize research opportunities.

Responsibilities include:

- Candidate ranking
- Opportunity filtering
- Rule-based scoring
- Watchlist generation

This capability recommends what deserves further investigation.

It does NOT:

- Execute trades
- Manage portfolios
- Allocate capital

---

# Capability Dependencies

| Capability | Depends On |
|------------|------------|
| Data Acquisition | External Systems |
| Data Processing | Data Acquisition |
| Domain Management | Data Processing |
| Market Analytics | Data Processing, Domain Management |
| Intelligence | Market Analytics, Domain Management |
| Research Platform | Market Analytics, Intelligence |
| Strategy | Research Platform |

---

# Mapping to Architecture

| Capability | Primary Architecture Modules |
|------------|------------------------------|
| Data Acquisition | Blockchain Collector, Aggregator Collector |
| Data Processing | Event Normalizer, Fact Engine |
| Domain Management | Entity Resolver, Metadata Engine |
| Market Analytics | Projection Engine, Feature Engine |
| Intelligence | Risk Engine |
| Research Platform | API Layer, Dashboard |
| Strategy | Strategy Engine |

---

# Capability Maturity

| Capability | MVP | Phase 2 | Phase 3 |
|------------|-----|---------|---------|
| Data Acquisition | ✓ | Expand Sources | Multi-chain Scaling |
| Data Processing | ✓ | Performance Optimization | Distributed Processing |
| Domain Management | ✓ | Advanced Metadata | Cross-chain Identity |
| Market Analytics | ✓ | Advanced Indicators | Streaming Analytics |
| Intelligence | ✓ (Rule-based) | Hybrid ML | Predictive Intelligence |
| Research Platform | ✓ | Collaboration | Multi-user Workspace |
| Strategy | ✓ (Rule-based) | ML Ranking | Adaptive Strategies |

---

# Future Capabilities (Out of MVP Scope)

The following capabilities are intentionally excluded from the MVP.

They are documented here to preserve architectural direction without introducing scope creep.

Future capabilities include:

- AI Platform
- Retrieval-Augmented Generation (RAG)
- Knowledge Graph
- Multi-Agent Orchestration
- Autonomous Research Agents
- Trade Execution Engine
- Portfolio Management
- Reinforcement Learning
- Cross-Chain Intelligence
- Online Learning

These capabilities must not influence MVP implementation decisions.

---

# Architectural Constraints

The following rules are mandatory.

- Capabilities communicate only through canonical contracts.
- Capabilities must remain independently testable.
- Historical Facts are immutable.
- State is always derived.
- Market Bars are derived exclusively from Blockchain Facts.
- Features must satisfy Point-in-Time Correctness.
- Capabilities never depend on technologies.
- Business logic remains framework-independent.

---

# Success Criteria

The MVP is considered successful when all seven capabilities operate together to transform raw blockchain activity into reliable, reproducible quantitative research.

The objective is not automated trading.

The objective is the construction of a trustworthy quantitative research platform capable of evolving toward autonomous intelligence in future phases.