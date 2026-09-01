# ML Data Cohort Status

> **Honest, current snapshot of the Base-chain training cohort for ML Foundation (Phase 4).**
> This document reflects the real state — including the hard gaps — so anyone picking this up knows exactly what data exists and what it would take to finish it.

## Current State

- **Durable pairs:** ~8 (target 200 → ≈4%)
- **Target block range:** `50_400_000 .. 50_549_999` — probe-verified high pair-creation density (**437 PairCreated** events)
- **Ingestion tooling:** `scripts/chunked_ingestion.py` (resumable, committed `7e9fac2`), 100-block chunks, state persisted to gitignored `scripts/ingestion_state.json`
- **Session record from Step 3:** 6 chunks completed through `50_405_599`, **2091 facts persisted, 8 pairs** created during that window.

## ⚠️ Critical Durability Caveat

The shared local TimescaleDB is **truncated by the integration/replay test suite** (`clean_entities`, `_clear_timescale`, replay reset). Measured immediately after a gate run, the DB held **1 pair / 3 facts / 0 features / 3 fixture outcomes** — not the 8/2091 from the Step 3 session.

**Consequence:** the ingested cohort is **not durable in this sandbox**. It persists only on a long-lived VM where tests do not wipe the tables. Any ML training that needs real cohort data must run against that VM's DB, or must explicitly train on seeded/fixture data (and say so).

## Class Balance

- **Unknown.** All 3 current outcomes are fixture (`RUG_PULL=False`, `DEAD_TOKEN=False`, `SUCCESSFUL_LAUNCH=True` — one each, 1h window). No new labels have been produced from freshly-ingested pairs because the outcome job has not been run against a durable cohort.
- **Action:** once a durable cohort exists, run `run_outcome_evaluation` over it and measure the RUG_PULL positive rate before setting a class-imbalance / anomaly strategy.

## Blockers

1. **~4.7-minute process limit** in the sandbox → each chunk is a short bounded run; full range needs ~1,500 blocks (≈150 chunks) to reach 200 pairs.
2. **No durable DB in sandbox** → cohort data is wiped by tests.
3. **No dedicated server** (Ubuntu on VMware only).

## Path Forward

| Option | Description | Effort | Verdict |
|--------|-------------|--------|---------|
| **A** | Continue chunked ingestion in the sandbox | ~250 sessions (slow, data lost on test runs) | ❌ Not recommended |
| **B** | Migrate to a long-lived VM, run ingestion + tests against separate DBs | Moderate | ✅ **Recommended** |
| **C** | Proceed with limited data for pipeline validation, expand later | Low | ✅ Do this now for ML Foundation's pipeline goal |

**Recommendation:** pursue **C** immediately (validate the ML pipeline with 8 pairs + fixture data, labeled honestly as *pipeline smoke*), and stand up **B** in parallel to grow a real, durable cohort.