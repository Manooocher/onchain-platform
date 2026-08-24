# Analytics Capability

Transforms finalized facts and observations into research-ready market bars, state projections, features, and outcomes.

## Components

| Module | Responsibility |
|--------|---------------|
| `trade_aggregator.py` | Generate OHLCV market bars from finalized `SWAP_EXECUTED` facts |
| `projection_engine.py` | Rebuild current pool state from facts (Redis-backed) |
| `feature_engine.py` | Compute deterministic, point-in-time-correct features |
| `outcome_engine.py` | Label ground-truth outcomes (RUG_PULL / SUCCESSFUL_LAUNCH / DEAD_TOKEN) |

## Data Flow

```
Finalized Facts
    ↓
State Projection (Redis cache) ──→ Observation Snapshots
    ↓
Market Bars (trade_aggregator)
    ↓
Feature Engineering
    ↓
Outcome Generation
```

## Features

### Currently Implemented (`feature_engine.py`)
- `liquidity_growth_pct_1h` — liquidity (% change) over a 1h window
- `price_momentum_zscore_1h` — price momentum z-score over a 1h window

### Feature Naming Convention
All feature names end with a unit suffix (DOC-012): `_pct`, `_ratio`, `_score`, `_zscore`, `_usd`, `_delta`.
Examples: `liquidity_growth_pct_1h`, `price_momentum_zscore_1h`.

### Point-in-Time Correctness
Features are only ever computed from data available at or before `as_of_timestamp` (no lookahead bias). `get_feature_at(session, entity_id, feature_name, as_of)` is the single code path for both backtest and live queries:

```python
from onchain_platform.persistence.timescale import repositories as ts_repo

feature = await ts_repo.get_feature_at(
    session,
    entity_id="eip155:8453/pair:0xabc...",
    feature_name="liquidity_growth_pct_1h",
    as_of=datetime(2026, 6, 1, tzinfo=UTC),
)
# Returns the feature valid as of 2026-06-01 — identical code path for live.
```

## Outcomes

Ground-truth labels applied once a pair's observation window closes (`outcome_rules.py`):
- `RUG_PULL` — liquidity collapse >90% or honeypot detected
- `SUCCESSFUL_LAUNCH` — sustained liquidity + activity, no honeypot
- `DEAD_TOKEN` — no swaps or drained reserves

Rules are deterministic and versioned:
```python
# outcome_rules.py
OUTCOME_RULES_VERSION = "1.0"
```

## Scheduling

- **Feature computation**: APScheduler hourly job (wired in `main.py`)
- **Outcome evaluation**: APScheduler hourly job — evaluates pairs whose observation window has closed, idempotently (`ON CONFLICT DO NOTHING`)