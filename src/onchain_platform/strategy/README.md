# Strategy Capability

Deterministic, rule-based ranking of research candidates (DOC-009 § Strategy). Strategy **recommends** what deserves further investigation — it does not execute trades, manage portfolios, or allocate capital.

## Components

| Module | Responsibility |
|--------|---------------|
| `ranking.py` | `compute_ranking(...)` — weighted-sum scoring, deterministic + explainable |
| `ranking_config.py` | Versioned weights / thresholds (Python constants, V1.0) |
| `api.py` | `build_strategy_router()` — the `/v1/strategy/rankings` router |

## Ranking Inputs

`compute_ranking(session, *, chain_id, dex, limit, as_of)` reads, per candidate pair:

- **Features** (PIT, via `list_latest_features`): `liquidity_growth_pct_1h`, `price_momentum_zscore_1h`
- **Risk signals**: a `HoneypotDetected` insight applies a fixed penalty
- **Outcomes** (sparse signal): a `SUCCESSFUL_LAUNCH=true` boosts; a `RUG_PULL=true` penalizes (only applied when a closed outcome exists)

Weights and caps live in `ranking_config.py` (`RANKING_RULES_VERSION = "1.0"`).

## Determinism & Explainability

- **Deterministic** (DOC-013): no wall-clock inside the engine (`as_of` is injected), no unseeded randomness, stable sort by `(score DESC, canonical_id ASC)`.
- **Explainable** (DOC-001): every `RankedCandidate` carries a `factors` list with each factor's `name`, normalized `value`, `weight`, and `contribution`.

## API

The router is **owned by the `strategy/` package**, not by `research/`, because DOC-011 forbids `research/` from importing `strategy/`. The composition root (`main.py`) injects it into the API app:

```python
from onchain_platform.research.api.main import create_app
from onchain_platform.strategy.api import build_strategy_router

app = create_app(extra_router=build_strategy_router())
```

Exposes:

```
GET /v1/strategy/rankings?chain_id=&dex=&limit=&as_of=
```

Returns a list of `RankedCandidate` sorted by score descending, each explainable.