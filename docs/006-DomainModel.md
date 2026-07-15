````markdown
---
id: DOC-006
title: Domain Model
version: 1.2
status: Draft
owner: CTO
last_updated: 2026-07-09
tags:
  - architecture
  - domain-model
  - data-platform
  - semantic-model
---

# Domain Model

> This document defines the semantic language of the platform.

Every module, database schema, API, pipeline, machine learning model, AI agent, and documentation file must use the concepts defined here.

This document is the semantic source of truth for the project.

---

# Purpose

The platform is not built around APIs or databases.

It is built around domain concepts.

Providers, databases, technologies, and implementations may change.

The semantic model should remain stable.

---

# Design Principles

The Domain Model is:

- Technology Independent
- Storage Independent
- Provider Independent
- AI Friendly
- Versionable
- Reproducible

Infrastructure depends on the Domain.

The Domain never depends on Infrastructure.

---

# Semantic Model

All semantic concepts are defined exclusively in DOC-008 (Canonical Glossary).

This document intentionally avoids redefining those concepts in order to prevent semantic drift.

The Core Entities described below are concrete implementations of the abstract concepts defined in the Canonical Glossary.


# Structural Domain

The following diagram describes business entities.

It does **not** describe processing order.

```text

Blockchain
    │
    ├─────────────┐
    ↓             ↓
Smart Contract   Wallet
    ↓             ↓
Token      Wallet Activity
    ↓
Trading Pair
    ↓
Liquidity Pool

```

Note: Structural entities and Temporal entities interact via references (e.g., a SwapExecuted Fact references a Liquidity Pool entity), but they follow independent lifecycles and must not be modeled as a single linear chain.


---

# Data Lifecycle

The following diagram describes how information flows through the platform.

```text
External Event
       ↓
Blockchain Fact
       ↓
State Projection
       ↓
Observation Snapshot
       ↓
Feature
       ↓
Outcome
       ↓
Insight

(See Market Data Pipeline for trade-derived analytics.)
```


Market Bars (OHLCV) are intentionally excluded from this pipeline.

They are generated independently from finalized Swap Facts.

---

# Market Data Pipeline

Market Bars follow a dedicated processing path.

```text
Swap Executed Facts

↓

Trade Aggregator

↓

Market Bars (OHLCV)

↓

Feature Engineering

↓

Research
```


This separation prevents sampling errors and preserves accurate OHLCV calculations.

---

# Core Entities

## Blockchain

Represents an EVM-compatible blockchain.

Examples:

- Ethereum
- Base
- BNB Chain

Responsibilities:

- Network identity
- Chain configuration
- Native asset definition

---

## Smart Contract

Represents executable on-chain logic.

Examples:

- ERC-20 Token
- Factory
- Router
- Liquidity Pool Contract

Smart Contracts emit blockchain events.

---

## Token

Represents a fungible blockchain asset.

Properties include:

- Contract Address
- Symbol
- Name
- Decimals
- Total Supply
- Deployment Block

A token may exist before becoming tradable.

---

## Trading Pair

Represents a tradable market.

Properties:

- Base Token
- Quote Token
- DEX
- Chain
- Creation Block

Trading Pair is the primary business object of the MVP.

---

## Liquidity Pool

Represents liquidity backing a Trading Pair.

Properties:

- Reserves
- Liquidity
- Fee Tier
- Protocol

Liquidity Pool state changes continuously.

---

## Wallet

Represents a blockchain account.

Examples:

- Developer Wallet
- Liquidity Provider
- Exchange Wallet
- Smart Money

Wallet itself stores identity.

Behavior is derived separately.

---

## Wallet Activity

Represents historical actions performed by a wallet.

Examples:

- Swap
- Transfer
- Mint
- Burn
- Liquidity Provision

Wallet Activity consists entirely of Blockchain Facts.

Behavioral analysis belongs to Feature Engineering.

---

## Blockchain Fact

Represents finalized blockchain history.

Examples:

- PairCreated
- SwapExecuted
- Mint
- Burn
- LiquidityAdded
- LiquidityRemoved

Properties:

- schema_version
- chain
- tx_hash
- block_hash
- block_number
- log_index
- event_time
- observed_at
- ingested_at
- confirmation_status


Lifecycle:

Pending

↓

Confirmed

↓

Finalized

or

Orphaned

Only Finalized Facts become immutable.


---

## State Projection

Represents the current calculated state of a domain object.

Examples:

- Pool Reserve
- Token Price
- Liquidity
- Holder Count

State is continuously updated from Blockchain Facts.

State is mutable.

---

## Observation Snapshot

Represents a timestamped snapshot of State.

Examples:

- Pool State
- Token State
- Liquidity Snapshot
- Holder Count Snapshot

Properties:

- Snapshot Timestamp
- Source
- Snapshot Version

Observation Snapshots preserve historical state for research.

---

## Market Bar

Represents aggregated market activity over a fixed time window.

Market Bars are **not** generated from Observation Snapshots.

Market Bars are generated directly from finalized SwapExecuted Facts.

Supported intervals include:

- 1 Minute
- 5 Minutes
- 15 Minutes
- 1 Hour

Typical fields:

- Open
- High
- Low
- Close
- Volume
- Trade Count

Market Bars are the primary input for quantitative research and technical indicators.

---

## Metadata

Represents contextual information that enriches entities.

Examples:

- Website
- Social Links
- Verification Status
- Logo
- Token Description

Metadata never changes Blockchain Facts.

---

## Feature

Represents deterministic analytical values.

Examples:

- Liquidity Growth
- Buy Pressure
- Wallet Concentration
- Price Momentum
- Volatility

Features are reproducible.

Features are point-in-time correct.

---

## Outcome

Represents future evaluation of an Observation.

Examples:

- Rug Pull
- Successful Launch
- Dead Token

Properties:

- Observation Window
- Label Definition
- Evaluation Timestamp
- Label Value

Outcome generation belongs to the Outcome Engine.

---

## Insight

Represents research-oriented conclusions.

Examples:

- Suspicious Liquidity Growth
- Whale Accumulation
- High Momentum
- Abnormal Trading Activity

Insights assist researchers.

Insights never become historical facts.

---

# Temporal Model

Time is a first-class concept.

Every temporal entity must distinguish between three timestamps:
- event_time (block_timestamp): Actual blockchain time.
- observed_at: Time the external provider emitted the data.
- ingested_at: Time our platform successfully received the data.

Historical correctness always has priority over processing convenience.

---

The platform follows the Point-in-Time Correctness and Blockchain Reorganization principles defined in DOC-008 (Canonical Glossary).

All pipelines and downstream systems must comply with these rules.

---

# Ownership

| Entity | Primary Owner |
|----------|-----------------------------|
| Blockchain | Blockchain Collector |
| Smart Contract | Metadata Service |
| Token | Entity Resolution |
| Trading Pair | Entity Resolution |
| Pool | Entity Resolution |
| Liquidity Pool | Projection Engine |
| Wallet | Wallet Service |
| Wallet Activity | Fact Extraction |
| Blockchain Fact | Fact Extraction |
| State Projection | Projection Engine |
| Observation Snapshot | Observation Engine |
| Market Bar | Trade Aggregator |
| Metadata | Metadata Service |
| Feature | Feature Engine |
| Outcome | Outcome Engine |
| Insight | Analytics Engine |

---

# Future Extensions

The following entities intentionally remain outside the MVP:

- Portfolio
- Position
- Order
- Strategy
- Signal
- Experiment
- Dataset
- Model Registry
- Knowledge Graph
- Reinforcement Learning Agent
- Prediction

These entities will be introduced in later phases.

---

# Guiding Principles

The platform is fundamentally a historical knowledge system.

Facts describe reality.

State describes the present.

Observations preserve history.

Market Bars summarize trading activity.

Features transform data.

Outcomes provide ground truth.

Insights help humans.

Every architectural decision should reinforce these semantics.
````
