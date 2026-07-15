````markdown
---
id: DOC-007
title: Data Flow
version: 1.2
status: Active
owner: CTO
last_updated: 2026-07-10
tags:
  - architecture
  - data-flow
  - event-driven
  - pipelines
---

# Data Flow

> This document defines how data flows through the platform.
>
> It describes the execution architecture of the system rather than the business domain.
>
> Semantic concepts (Fact, State, Observation, Feature, Outcome, Prediction, etc.) are defined exclusively in **DOC-008 (Canonical Glossary)**.

---

# Objectives

The platform is designed around deterministic, event-driven processing.

Every processing stage has a single responsibility and transforms data into a higher-level representation without violating historical correctness.

The architecture aims to achieve:

- Deterministic Processing
- Reproducibility
- Explainability
- Point-in-Time Correctness
- Temporal Consistency
- Storage Separation
- Provider Independence
- AI-Friendly Data Pipelines

---

# High-Level Execution Flow

```text
                  External Sources
                         │
                         ▼
                  Data Collectors
                         │
                         ▼
                  Event Processing
                         │
                         ▼
                 Blockchain Facts
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
 Entity Resolution   Projection Engine  Trade Aggregator
        │                │                │
        ▼                ▼                ▼
 PostgreSQL     Observation Snapshots   Market Bars
                        │                │
                        └───────┬────────┘
                                ▼
                       Feature Engineering
                                │
                                ▼
                        Outcome Generation
                                │
                                ▼
                     Research / ML Dataset
````

---

# Pipeline Overview

The platform consists of six logical processing pipelines.

Each pipeline has an independent responsibility.

## Pipeline 1

Historical Fact Pipeline

Responsible for converting external events into immutable blockchain facts.

---

## Pipeline 2

Market Analytics Pipeline

Responsible for transforming finalized trade facts into Market Bars.

---

## Pipeline 3

State Observation Pipeline

Responsible for projecting blockchain state into historical Observation Snapshots.

---

## Pipeline 4

Entity & Metadata Pipeline

Responsible for creating and enriching persistent business entities.

---

## Pipeline 5

Feature Engineering Pipeline

Responsible for transforming historical data into reproducible analytical features.

---

## Pipeline 6

Outcome Generation Pipeline

Responsible for producing ground-truth labels for quantitative research.

---

# Pipeline 1 — Historical Fact Pipeline

Purpose:

Create a reproducible history of blockchain activity.

Flow:

```text
External Event

↓

Decoder

↓

Normalizer

↓

Validator

↓

Fact Extraction

↓

Blockchain Fact

↓

Persistence
```

Examples of Blockchain Facts:

* PairCreated
* SwapExecuted
* LiquidityAdded
* LiquidityRemoved
* Mint
* Burn

Blockchain Facts represent objective historical truth.

Only finalized Facts should be treated as immutable.

Blockchain reorganization handling follows the rules defined in **DOC-008**.

---

# Pipeline 2 — Market Analytics Pipeline

Purpose:

Transform finalized trade activity into quantitative market data.

Flow:

```text
Finalized SwapExecuted Facts

↓

Trade Aggregator

↓

OHLCV Builder

↓

Market Bars

↓

TimescaleDB
```

Market Bars are derived exclusively from finalized SwapExecuted Facts.

They are never generated from Observation Snapshots.

Typical outputs:

* OHLC
* Volume
* Trade Count
* VWAP
* TWAP
* Buy Volume
* Sell Volume

---

# Pipeline 3 — State Observation Pipeline

Purpose:

Capture historical snapshots of projected blockchain state.

Flow:

```text
Blockchain Facts

↓

Projection Engine

↓

Current State

↓

Observation Snapshot

↓

TimescaleDB
```

Typical observations include:

* Current Liquidity
* Pool Reserves
* Holder Count
* Market Capitalization
* FDV

Observation Snapshots preserve historical state.

They are not analytical aggregations.

---

# Pipeline 4 — Entity & Metadata Pipeline

Purpose:

Maintain the operational representation of the business domain.

Flow:

```text
Normalized Data

↓

Entity Resolution

↓

Metadata Enrichment

↓

PostgreSQL
```

Managed entities include:

* Token
* Trading Pair
* Liquidity Pool
* Wallet
* Smart Contract

Metadata examples:

* Website
* Logo
* Social Links
* Verification Status

Metadata enriches entities without modifying historical Facts.

---

# Pipeline 5 — Feature Engineering Pipeline

Purpose:

Generate deterministic analytical variables.

Inputs:

* Observation Snapshots
* Market Bars
* Metadata

Flow:

```text
Historical Data

↓

Feature Engine

↓

Feature Store
```

Example features:

* Liquidity Growth
* Holder Growth
* Buy Pressure
* Wallet Concentration
* Rolling Volatility
* Momentum Indicators

Features must remain deterministic and reproducible.

The platform follows Point-in-Time Correctness as defined in **DOC-009**.

---

# Pipeline 6 — Outcome Generation Pipeline

Purpose:

Generate historical ground-truth labels.

Flow:

```text
Observation

↓

Observation Window

↓

Rule Evaluation

↓

Outcome

↓

PostgreSQL
```

Typical Outcomes:

* Rug Pull
* Successful Launch
* Dead Token

Outcomes represent historical truth.

Predictions are intentionally excluded from this pipeline.

---

# Storage Responsibilities

The platform intentionally separates operational storage from analytical storage.

## PostgreSQL

Stores:

* Tokens
* Trading Pairs
* Liquidity Pools
* Wallets
* Smart Contracts
* Metadata
* Outcomes

---

## TimescaleDB

Stores:

* Observation Snapshots
* Market Bars
* Time-Series Features

---

Historical Blockchain Facts may initially reside in PostgreSQL during the MVP.

Future versions may migrate Facts into dedicated append-only storage.

---

# Temporal Consistency

Every temporal record should distinguish between three timestamps.

## event_time

Actual blockchain time.

Typically the block timestamp.

Used for:

* Historical ordering
* Backtesting
* Quantitative Research

---

## observed_at

Time the external provider observed or emitted the data.

Used for:

* Provider latency
* Feed diagnostics

---

## ingested_at

Time the platform successfully ingested the data.

Used for:

* Pipeline monitoring
* Performance analysis
* Debugging

---

# Data Contracts

Every module communicates exclusively through Canonical Schemas.

Modules must never depend directly on:

* RPC responses
* Provider-specific payloads
* Third-party API structures

Canonical Schemas are defined in **DOC-008**.

---

# Error Isolation

Pipeline failures should remain isolated.

Examples:

* RPC timeout
* Metadata provider unavailable
* API rate limiting
* Network interruption

Operational failures must never corrupt historical Facts.

Graceful degradation is preferred over cascading failures.

---

# Design Principles

The platform follows these architectural principles:

* Event-Driven Processing
* Single Responsibility
* Immutable Historical Facts
* Deterministic Feature Engineering
* Point-in-Time Correctness
* Temporal Consistency
* Canonical Schemas
* Storage Separation
* Provider Independence

---

# Future Extensions

Future versions of the platform may introduce:

* Multi-chain Processing
* Distributed Collectors
* Streaming Feature Engine
* Online Feature Store
* Prediction Pipeline
* Reinforcement Learning Pipeline
* Real-Time Alert Engine
* Strategy Execution Engine

These additions should integrate without changing the existing pipeline architecture.

---

# Guiding Principle

Every processing stage increases semantic value.

```text
External Events
        │
        ▼
Blockchain Facts
        │
        ├──────────────► Market Bars
        │
        ▼
Observation Snapshots
        │
        └──────────────► Features
                         │
                         ▼
                      Outcomes
                         │
                         ▼
               Research & Machine Learning
```

The platform transforms raw blockchain activity into reproducible quantitative knowledge while preserving historical correctness at every stage.

```
```
