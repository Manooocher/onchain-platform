````markdown
---
id: DOC-004
title: System Architecture
version: 1.0
status: Draft
owner: CTO
last_updated: 2026-07-08
tags:
  - architecture
  - system-design
  - mvp

---

# System Architecture

> **Build a modular, event-driven research platform where every component has a single responsibility and every data flow is observable, replaceable, and extensible.**

---

# Purpose

This document defines the logical architecture of the system.

It describes:

* system boundaries
* module responsibilities
* data flow
* processing pipeline
* architectural principles

It intentionally avoids implementation details and technology choices.

---

# Architecture Goals

The architecture should:

* collect blockchain data continuously
* process events reliably
* support incremental feature development
* remain maintainable by a single developer
* evolve without large-scale rewrites
* isolate modules through explicit contracts

---

# Non Goals

This architecture does **not** attempt to solve:

* microservice deployment
* distributed computing
* horizontal scaling
* high-frequency execution
* multi-region infrastructure

These concerns belong to future phases.

---

# High-Level Data Flow

```text
                External Sources
                       │
                       ▼
                 Data Collectors
                       │
                       ▼
               Event Transport Layer
                       │
                       ▼
              Normalization Pipeline
                       │
                       ▼
                Persistence Layer
                ┌────────┴────────┐
                ▼                 ▼
        Feature Engineering   Metadata Service
                └────────┬────────┘
                         ▼
                  Analytics Engine
                   ┌──────────────┐
                   ▼              ▼
               REST API      ML Pipeline
                   │
                   ▼
               Dashboard/UI
```

Every component consumes structured input and produces structured output.

No module should depend on another module's internal implementation.

---

# System Modules

## 1. External Sources

Purpose:

Provide raw blockchain and market information.

Examples include:

* blockchain RPC providers
* blockchain websocket providers
* aggregator APIs
* metadata providers

This layer is outside our control.

---

## 2. Data Collectors

Purpose:

Continuously acquire raw observations.

Responsibilities:

* polling APIs
* websocket subscriptions
* request scheduling
* retry logic
* rate limit handling
* provider failover

Collectors perform **no business logic**.

Output:

Raw Events.

---

## 3. Event Transport Layer

Purpose:

Separate event producers from event consumers.

Responsibilities:

* decouple modules
* absorb traffic bursts
* isolate processing speed differences
* provide reliable event delivery
* support future scalability

This layer represents an abstraction rather than a specific implementation.

Its implementation may evolve without affecting the rest of the architecture.

---

## 4. Normalization Pipeline

Purpose:

Convert heterogeneous provider responses into canonical domain objects.

Responsibilities:

* schema validation
* field mapping
* timestamp normalization
* type conversion
* duplicate detection
* data quality checks

After this stage the rest of the platform never depends on provider-specific formats.

---

## 5. Persistence Layer

Purpose:

Persist normalized observations.

Responsibilities:

* historical storage
* indexing
* querying
* transactional consistency

This layer stores facts.

It does not generate insights.

---

## 6. Feature Engineering

Purpose:

Transform stored observations into reusable research features.

Examples:

* price velocity
* liquidity growth
* buy/sell ratio
* transaction rate
* market age

Feature engineering should remain deterministic during MVP.

---

## 7. Metadata Service

Purpose:

Attach contextual information to domain entities.

Examples:

* token metadata
* protocol metadata
* contract metadata
* chain metadata

Metadata changes less frequently than market events and follows a separate lifecycle.

---

## 8. Analytics Engine

Purpose:

Generate research-oriented insights.

Responsibilities:

* ranking
* filtering
* scoring
* deterministic analysis

Machine learning is intentionally outside this module.

---

## 9. Machine Learning Pipeline

Purpose:

Consume historical datasets produced by the platform.

Responsibilities:

* dataset generation
* offline training
* model evaluation
* inference

ML consumes platform data.

It never owns the data pipeline.

---

## 10. Presentation Layer

Purpose:

Expose information to users.

Examples:

* REST API
* dashboard
* research interface

Presentation should never contain business logic.

---

# Data Lifecycle

Every observation follows the same lifecycle.

```text
Observe

↓

Collect

↓

Transport

↓

Normalize

↓

Validate

↓

Persist

↓

Generate Features

↓

Analyze

↓

Present
```

Every future capability should reuse this lifecycle.

---

# Domain Ownership

Each module owns a single responsibility.

| Module              | Owns                   |
| ------------------- | ---------------------- |
| Collectors          | External communication |
| Event Layer         | Event transport        |
| Normalizer          | Canonical data         |
| Persistence         | Historical facts       |
| Feature Engineering | Derived features       |
| Metadata            | Context                |
| Analytics           | Research insights      |
| ML Pipeline         | Predictive models      |
| API                 | Data delivery          |
| Dashboard           | Visualization          |

No responsibility should exist in multiple modules.

---

# Replaceable Components

Every external dependency should remain replaceable.

Examples:

* RPC providers
* aggregator providers
* storage implementation
* event transport implementation
* ML framework
* dashboard framework

Business logic must remain independent of infrastructure choices.

---

# Evolution Strategy

The MVP is implemented as a modular monolith.

As the system grows, individual modules may be extracted into independent services **only if** operational requirements justify the additional complexity.

Architecture should support evolution without requiring large-scale rewrites.

---

# Guiding Principles

* Prefer simple solutions over sophisticated ones.
* Prefer explicit contracts over implicit coupling.
* Prefer deterministic pipelines over hidden side effects.
* Prefer replaceable infrastructure over framework lock-in.
* Optimize only after identifying real bottlenecks.
* Every module should be independently testable.
* Every processing step should be observable.
* Complexity must be introduced incrementally.

---

# Architectural Summary

The platform is fundamentally a **data processing pipeline**.

Its primary responsibility is to transform raw blockchain activity into structured, reusable research knowledge.

Everything else—including analytics, machine learning, agents, and trading—is built on top of that foundation.
