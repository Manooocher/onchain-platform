# Onchain Platform

**AI-Native Quantitative Research Platform for Decentralized Markets**

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-331%20passing-brightgreen.svg)](tests/)
[![Code Style](https://img.shields.io/badge/Code%20Style-Ruff-black.svg)](https://github.com/astral-sh/ruff)
[![Type Checking](https://img.shields.io/badge/Type%20Checking-mypy-blue.svg)](http://mypy-lang.org/)

> Transform raw blockchain activity into reproducible quantitative knowledge.

---

## Overview

Onchain Platform is an end-to-end research system that continuously observes EVM blockchains (Ethereum, Base, BNB Chain), converts raw events into deterministic, auditable facts, and derives research-ready features, ground-truth outcomes, and ranked opportunities.

It exists to replace the fragmented workflow researchers currently use — stitching together block explorers, spreadsheets, and ad-hoc scripts — with one platform that is **deterministic** (same inputs → identical outputs), **reproducible** (replay produces byte-identical results), **point-in-time correct** (no lookahead bias in derived analytics), and **explainable** (every ranking and insight traces back to its inputs).

The pipeline spans: **data acquisition → facts → state projections → snapshots → features → outcomes → research datasets → strategy ranking**, exposed through a read-only FastAPI and a Streamlit dashboard. It is built for quantitative researchers and blockchain analysts who need trustworthy, queryable ground truth — not for automated trading (which is explicitly out of scope).

---

## Where We Are

- **Milestones 1–10: complete and verified** — walking skeleton → finality → market bars → domain management → state projection → feature engineering → intelligence (GoPlus risk) → outcome engine → research API/dashboard → strategy ranking. MVP exit criterion met (DOC-003).
- **ML Foundation (Phase 4): next phase, Base chain only.**
  - **Documentation complete**: execution plan, cohort status, and model cards.
  - **Phase 0 prerequisites done**: 5 PIT-correct features, 24h observation window + parameterized thresholds, chunked historical cohort-ingestion tooling.
  - **Implementation: not started** — the `ml/` package does not yet exist.
- **Data cohort: partially collected.** The historical pair cohort is ~8 pairs vs a 200-pair target (4%) and, critically, **is not durable in the sandbox** (the integration/replay test suite truncates the shared local tables). See `docs/ML_DATA_COHORT.md`.

For the full state, see the [ML Foundation Execution Plan](docs/implementation/MLFoundation-ExecutionPlan.md), the [Implementation Plan](docs/implementation/ImplementationPlan.md), and the [current limitations](#known-limitations).

---

## Features

### 🔍 Data Acquisition & Processing
- **Real-time blockchain event collection** from EVM chains (Ethereum, Base, BNB Chain)
- **Deterministic fact extraction** with confirmation lifecycle (Pending → Confirmed → Finalized)
- **Chain reorganization detection** and automatic handling (multi-block reorg support)
- **Idempotent processing** — replay produces identical results

### 📊 Market Analytics
- **OHLCV market bars** generated directly from finalized swap facts (not snapshots)
- **State projection** with Redis-backed real-time cache
- **Observation snapshots** preserving historical state for research
- **Feature engineering** with Point-in-Time correctness (no lookahead bias) — **5 PIT-correct features**:
  - `liquidity_growth_pct_1h`
  - `price_momentum_zscore_1h`
  - `volume_quote_delta_1h`
  - `honeypot_detected_score`
  - `liquidity_usd_delta_1h`
- **Outcome labeling** across **1h and 24h observation windows** with parameterized, window-aware thresholds (e.g. an 80% liquidity drop is a 24h rug pull but not a 1h one)

### 🎯 Intelligence & Strategy
- **Risk analysis** via deterministic rule engine (GoPlus integration)
- **Candidate ranking** with explainable scoring factors
- **Insight generation** for research assistance

### 🖥️ Research Platform
- **FastAPI REST API** with OpenAPI documentation
- **Streamlit dashboard** for interactive exploration
- **Cursor-based pagination** for stable data access
- **Point-in-Time queries** for backtesting and research

---

## Architecture

The platform is a **modular monolith** with strict dependency boundaries enforced by import-linter contracts.

```
External Sources (RPC/WebSocket)
         ↓
   Data Acquisition (providers, collector)
         ↓
   Data Processing (normalizer, finality engine)
         ↓
   Domain Management (entity resolution, metadata)
         ↓
   Market Analytics (projection, bars, features)
         ↓
   Intelligence (risk rules, insights)
         ↓
   Strategy (ranking, filtering)
         ↓
   Research Platform (API, dashboard)
         ↓
   Machine Learning (ml/*, Phase 4 — documented, not yet implemented)
```

**Machine Learning (Phase 4)** is a read-only capability that will read from `analytics/` (features, snapshots, outcomes) and `persistence/`, and will **never write to `blockchain_facts`** (append-only) or mutate a fact/outcome. See the [execution plan](docs/implementation/MLFoundation-ExecutionPlan.md).

### Data Flow

```
Blockchain Events → Facts → State Projections → Snapshots → Features → Outcomes → Research Datasets
```

### Storage Strategy

- **PostgreSQL (TimescaleDB)**: Operational entities (Tokens, Pairs, Wallets, Outcomes, Insights) **and** time-series hypertables (Market Bars, Snapshots, Features)
- **Redis**: State cache, event transport, rate limiting

---

## Quick Start

### Prerequisites
- Python 3.12+
- Docker & Docker Compose
- `uv` (Python package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/onchain-platform.git
   cd onchain-platform
   ```

2. **Install dependencies**
   ```bash
   uv sync
   ```

3. **Set up environment**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

4. **Start infrastructure** (TimescaleDB on `:5433`, Redis on `:6379`)
   ```bash
   docker compose up -d
   ```

5. **Run migrations**
   ```bash
   uv run alembic upgrade head
   ```

6. **Start the platform**
   ```bash
   uv run python -m onchain_platform.main
   ```

7. **Start the API + dashboard** (two terminals)
   ```bash
   uv run uvicorn onchain_platform.research.api.main:create_app --factory --port 8000
   uv run streamlit run src/onchain_platform/research/dashboard/app.py
   ```

8. **Access**
   - API: http://localhost:8000
   - API Docs (interactive): http://localhost:8000/docs
   - OpenAPI spec: http://localhost:8000/v1/openapi.json
   - Dashboard: http://localhost:8501

### Collector CLI

The ingestion process accepts a `--chain` flag and an optional block range. With
multi-provider API keys set in `.env`, it builds a failover pool (Alchemy primary,
QuickNode secondary, RockX/W3Node tertiary); without keys it falls back to the
public RPC endpoint.

```bash
# Collect from Base chain (default)
uv run python -m onchain_platform.main

# Collect from a specific chain (provider pool from config/providers.yaml)
uv run python -m onchain_platform.main --chain base
uv run python -m onchain_platform.main --chain ethereum
uv run python -m onchain_platform.main --chain bnb

# Process a specific block and exit
uv run python -m onchain_platform.main --chain base --start-block 50000000

# Process a bounded block range (chunked historical ingestion)
uv run python -m onchain_platform.main --chain base --start-block 50400000 --end-block 50400099
```

### Historical Cohort Ingestion (ML Foundation data)

Chunked, resumable ingestion tooling builds a real pair cohort for ML Foundation training data:

- [`scripts/chunked_ingestion.py`](scripts/chunked_ingestion.py) — resumable, small-chunk ingestion using the **production collector path** (state persisted to a gitignored `scripts/ingestion_state.json`)
- [`scripts/probe_pair_density.py`](scripts/probe_pair_density.py) — locates high pair-creation density ranges
- [`scripts/cohort_config.py`](scripts/cohort_config.py) — the target block range + chunk size

```bash
uv run python scripts/chunked_ingestion.py --one-chunk       # smoke-test one chunk
uv run python scripts/chunked_ingestion.py --time-budget 240 # run until ~240s
```

See [`docs/ML_DATA_COHORT.md`](docs/ML_DATA_COHORT.md) for cohort status and how to complete it on a long-lived VM.

---

## Project Structure

```
onchain_platform/
├── src/onchain_platform/          # Main package
│   ├── domain/                    # Domain models & canonical schemas
│   │   ├── schemas/              # Canonical schemas (Facts, Features, Outcomes)
│   │   └── entities/             # Domain entities (Token, Pair, Wallet)
│   ├── acquisition/              # Data collection (providers, collector)
│   ├── processing/               # Fact extraction, finality, normalization
│   ├── domain_management/        # Entity resolution, metadata
│   ├── analytics/                # Features, projections, outcomes
│   ├── intelligence/             # Risk rules, insights
│   ├── strategy/                 # Candidate ranking
│   ├── research/                 # API & dashboard
│   │   ├── api/                  # FastAPI endpoints
│   │   └── dashboard/            # Streamlit UI
│   ├── ml/                       # Machine Learning (Phase 4 — planned, not yet implemented)
│   │   ├── datasets/            # dataset assembly, splits, normalization
│   │   ├── models/              # classifiers & regressors (sklearn/XGBoost)
│   │   ├── training/            # trainer, hyperparameter tuning
│   │   ├── tracking/            # MLflow, metrics, model registry
│   │   └── serving/             # prediction API
│   ├── persistence/              # Database repositories
│   ├── transport/                # Event streams, state cache
│   └── platform/                 # Config, logging, scheduler
├── tests/                        # Test suites
│   ├── unit/                     # Unit tests
│   ├── integration/              # Integration tests
│   ├── replay/                   # Determinism replay tests
│   └── schema/                   # Schema validation tests
├── migrations/                   # Alembic migrations
├── docs/                         # Documentation
│   ├── adr/                      # Architecture Decision Records
│   └── implementation/           # Implementation plans
└── config/                       # Per-chain configuration (YAML)
```

---

## Testing

The platform maintains comprehensive test coverage. Run via the Makefile:

```bash
make lint          # Code style + formatting (ruff)
make typecheck     # Type checking (mypy, strict)
make import-check  # Dependency contracts (import-linter)
make test          # unit + integration + schema
make test-replay   # Determinism verification (byte-identical outputs)
```

**Verified counts (current HEAD):**
- **331 unit + integration + schema** passing (+2 env-gated live skips), **7 replay**, **1 live smoke**.

**Test Breakdown:**
- **Unit tests**: Core logic, schema validation
- **Integration tests**: End-to-end flows with real databases
- **Replay tests**: Determinism verification (byte-identical outputs)
- **Live smoke tests**: Production readiness checks (marked `-m live`)

Run individually:
```bash
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest -m live tests/integration/test_live_smoke.py
```

---

## Configuration

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `RPC_URL` | Blockchain RPC endpoint | Yes | `https://mainnet.base.org` |
| `POSTGRES_DSN` | PostgreSQL/TimescaleDB DSN (async) | Yes | `postgresql+asyncpg://onchain@localhost:5433/onchain_platform` |
| `REDIS_URL` | Redis connection string | Yes | `redis://localhost:6379/0` |
| `ALCHEMY_API_KEY` | Alchemy API key (optional provider) | No | — |

### Chain Configuration

Per-chain confirmation depth is read from `config/confirmation_depth.yaml` (ADR-006 § Configurable Confirmation Depth):

```yaml
confirmation_depth:
  ethereum: 12
  base: 3
  bnb: 8
```

---

## Known Limitations

Honest current constraints (see [ML Data Cohort Status](docs/ML_DATA_COHORT.md) and the [execution plan](docs/implementation/MLFoundation-ExecutionPlan.md)):

- **Data cohort is ~8 pairs (4% of the 200-pair target) and is not durable in the sandbox.** The integration/replay test suite truncates the shared local TimescaleDB tables, so the live DB typically holds only a small fixture set (e.g. 1 pair / a handful of facts). A durable cohort requires running on a long-lived VM where tests do not wipe the tables.
- **Infrastructure:** Ubuntu on VMware only — no dedicated server. Long-lived processes are terminated after ~4.7 minutes, which is why cohort ingestion is chunked and resumable.
- **ML Foundation:** documentation is complete and Phase 0 prerequisites (5 features, 24h window, chunked ingestion tooling) are done, but **implementation has not started** — there is no `ml/` package or trained model yet.
- **Class balance is unknown:** the RUG_PULL positive rate has not been measured on a durable cohort, so no training threshold or anomaly-detection decision can be set yet.

**Next steps:** complete the data cohort on a long-lived VM, then implement the ML Foundation pipeline (see the [execution plan](docs/implementation/MLFoundation-ExecutionPlan.md)).

---

## Documentation

- [ML Foundation Execution Plan](docs/implementation/MLFoundation-ExecutionPlan.md)
- [ML Data Cohort Status](docs/ML_DATA_COHORT.md)
- [ML Model Cards](docs/ML_MODELS.md)
- [Architecture Overview](docs/ARCHITECTURE.md)
- [API Reference](docs/API.md)
- [Developer Guide](docs/DEVELOPMENT.md)
- [Domain Model](docs/006-DomainModel.md)
- [Canonical Schemas](docs/012-CanonicalSchema.md)
- [Coding Standards](docs/013-CodingStandards.md)
- [Implementation Plans](docs/implementation/)
- [Architecture Decision Records](docs/adr/)

---

## Contributing

Contributions are welcome! Please read our [Contributing Guide](CONTRIBUTING.md) before submitting PRs.

### Development Workflow
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Ensure all quality gates pass (`make lint typecheck import-check test`)
5. Submit a pull request

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/), [Streamlit](https://streamlit.io/), and [Polars](https://pola.rs/)
- Risk data provided by [GoPlus Security](https://gopluslabs.io/)
- Blockchain data via public RPC endpoints (e.g. Base)
