---
name: domain
description: Use when writing or modifying anything under src/onchain_platform/domain/** or tests/schema/**, or when a task involves a Canonical Schema, a composite ID, or the Decimal/float boundary. Full spec: docs/012-CanonicalSchema.md.
---

# Domain — Canonical Schemas & Entities

Field types, storage-fate categories (B.0–B.5), and every naming convention live in docs/012-CanonicalSchema.md — this file points at it rather than summarizing it, so it can't quietly drift out of sync.

- `entities/` = Part A (Blockchain, SmartContract, Token, TradingPair, LiquidityPool, Wallet, Metadata) — slowly-changing registry objects.
- `schemas/` = Part B (Checkpoint § B.0, BlockchainFact § B.1, StateProjection § B.2, ObservationSnapshot/MarketBar/Feature § B.3, Outcome/Insight § B.4, ChainReorgEvent § B.5) — temporal or derived.
- Every model here is `frozen=True` (Pydantic `model_config`). A state transition is `model_copy(update=...)`, never in-place mutation.
- Composite IDs join fields with `|`, not `:` — Canonical IDs (`eip155:8453/pair:0x...`) and ISO timestamps both already contain `:`, so `:` can't round-trip through a split.
- Token Amounts (`amount0`, `reserve0`, `total_supply`, ...) are `str`-typed `Decimal`, always a positive magnitude — direction comes from `fact_type`, never from the sign of the number.
- `domain/` has zero imports from anywhere else in this repo — not `persistence`, not `acquisition`, not an ORM or vendor type. If you're reaching for one of those in here, that's an import-linter violation about to happen, not a shortcut.
