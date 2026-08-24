# Domain Layer

The foundation of the platform — all business concepts, canonical schemas, and domain entities. `domain/` depends on **nothing** else in the repository (DOC-006, DOC-011); every other package imports from it.

## Structure

```
domain/
├── schemas/          # Canonical schemas — temporal concepts (DOC-012 Part B)
│   ├── blockchain_fact.py
│   ├── market_bar.py
│   ├── observation_snapshot.py
│   ├── feature.py
│   ├── outcome.py
│   ├── insight.py
│   ├── ranking.py
│   └── enums.py
├── entities/         # Domain entities — structural concepts (DOC-012 Part A)
│   ├── token.py
│   ├── trading_pair.py
│   ├── liquidity_pool.py
│   ├── wallet.py
│   ├── smart_contract.py
│   └── metadata.py
├── exceptions.py     # Platform-wide exception hierarchy (PlatformError)
├── enums.py          # Structural enums (ChainId, EntityType, ContractType)
└── ids.py            # Canonical ID construction (eip155:<chain>/<type>:<addr>)
```

## Key Principles

1. **Technology Independent** — no imports from infrastructure/persistence packages
2. **Immutable** — all schemas set `frozen=True` (Pydantic); state change is `model_copy(update=...)`, never mutation
3. **Financial Precision** — monetary values are `Decimal`/`str`, not `float` (DOC-008)
4. **Point-in-Time Correct** — derived values never look ahead of their `as_of` timestamp

## Canonical Schemas

See [DOC-012 Canonical Schema Specification](../../docs/012-CanonicalSchema.md) for the authoritative field-by-field definitions.

### Temporal Schemas (`schemas/`)
- **BlockchainFact** — verified historical on-chain events (append-only once finalized)
- **MarketBar** — OHLCV aggregations of finalized swap facts
- **ObservationSnapshot** — historical state recordings
- **Feature** — deterministic analytical transformations
- **Outcome** — ground-truth labels (Rug Pull / Successful Launch / Dead Token)
- **Insight** — human-readable research conclusions
- **RankedCandidate / RankingFactor** — explainable strategy ranking output

### Structural Entities (`entities/`)
- **Token** — fungible asset
- **TradingPair** — a tradable market (base/quote)
- **LiquidityPool** — liquidity backing a pair
- **Wallet** — blockchain account
- **SmartContract** — executable on-chain logic
- **Metadata** — enrichment (website, socials, verification status)

## Usage

```python
from onchain_platform.domain.schemas.blockchain_fact import BlockchainFact, SwapExecutedPayload
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType

fact = BlockchainFact(
    fact_id="8453:0xabc...:14",
    chain_id=8453,
    fact_type=FactType.SWAP_EXECUTED,
    block_number=18234599,
    block_hash="0x71ab...",
    tx_hash="0x9f2c...",
    log_index=14,
    event_time=...,
    observed_at=...,
    ingested_at=...,
    confirmation_status=ConfirmationStatus.PENDING,
    confirmations=0,
    payload=SwapExecutedPayload(
        fact_type="SWAP_EXECUTED",
        pool_address="0x88e6...",
        sender="0x1234...",
        recipient="0x1234...",
        amount0_in="0",
        amount1_in="500000000000000000",
        amount0_out="1230000000000000000000",
        amount1_out="0",
    ),
)
```

Entities and schemas are read/written through `persistence/` repositories — never accessed directly as ORM models (DOC-011).