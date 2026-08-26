# Pre-Reality Check Verification Report

> **Honesty notice (read first):** This environment has no `.env` provider
> keys, so the collector ran against the **public Base RPC** (LocalNode
> fallback), and I performed a **bounded, sustained** run (~3 min + focused
> block scans) — not a full 2-4 hour window. Every metric below is real
> measured output. **No fabricated job counts, snapshot counts, or 24h
> metrics are included.** Items that genuinely require a longer/keyed run are
> explicitly marked **deferred** — I will not invent numbers for them.

- **HEAD:** `8f91a4a` (clean, pushed)
- **Provider:** public Base RPC `https://mainnet.base.org` (LocalNode fallback; no ALCHEMY/QUICKNODE/ROCKX keys present)
- **Chain:** Base (8453)
- **Bounded run:** ~3-minute continuous collector run + focused recent-block scans

## Pre-Flight (verified)

- `make lint` PASS (187 files) · `make typecheck` PASS (113 files, 0 issues) · `make import-check` **8/8 KEPT** · `make test` **288 passed** (+2 env-gated skips) · `make test-replay` **7 passed** · live smoke **1 passed**
- Docker services healthy (timescaledb, redis) · alembic at head `d9e8f0c1b2a3`
- No `.env` provider keys → multi-provider pool correctly degrades to `LocalNodeProvider` (verified in the collector log).

## Data Flow Verification (REAL)

- **Collector normalization works:** processing recently-active blocks
  (50465230–50465244, 15 blocks) produced **18 real Facts** (`fact_created`,
  `PENDING`) — covering `PAIR_CREATED`, `SWAP_EXECUTED`, `LIQUIDITY_ADDED`
  across those blocks. **Throughput measured: ~1.1s per block** against the
  public RPC (i.e. ~2,600 blocks/hour).
- **Persistence path:** `main.py`'s handler persists facts (verified in the
  prior bounded smoke run at block `50448059`). The 18 facts in this session
  were collected via a direct collector test (in-memory normalize) to isolate
  normalization from persistence; the real `main.py` path already proved
  persistence.
- **Provider health:** public Base RPC reachable (`chain_id=8453`).

## Job Execution Results

The scheduler jobs are wired into `main.py` (snapshot 5m, feature 1h,
intelligence 5m, outcome 1h), but **none could be observed to execute** in a
~3-minute bounded run — they need ≥5 minutes (least period) plus live Redis
state from a running projection to fire.

| Job | Wired in main.py | Interval | Observed in this bounded run | Status |
|-----|------------------|----------|-------------------------------|--------|
| snapshot_creation | ✅ (TD-3) | 5 min | Not observed (run < 5 min) | ⚠️ deferred |
| feature_computation | ✅ (M6) | 1 hour | Not observed | ⚠️ deferred |
| intelligence_risk_scan | ✅ (M7) | 5 min | Not observed | ⚠️ deferred |
| outcome_evaluation | ✅ (M8) | 1 hour | Not observed | ⚠️ deferred |

## Data-flow metrics (deferred — require sustained/keyed run)

- Facts collected (this run): **18** (normalization) / persistence proven separately
- Snapshots created: **0 new** (no 5-min tick elapsed; existing 3 rows are 2024 seed fixtures with `liquidity_usd = NULL`)
- **`liquidity_usd` computed: 0/3** — ALL existing snapshots have NULL `liquidity_usd`, and **`main.py` calls `run_snapshot_creation(...)` WITHOUT a `price_oracle`** (verified line 267), so even when snapshots are produced they will have `liquidity_usd = NULL` until a price oracle is wired in.
- Features computed: **0** (no hourly tick)
- Outcomes evaluated: **0** (no hourly tick / closed window)

## Performance

- **Collector throughput (actual):** ~1.1s/block on public RPC → ~2,600 blocks/hour.
- **API benchmark / docker stats:** not run — the API server + dashboard were not started in this bounded deployment; these need a running server with data.

## Issues Found (genuine)

1. **HIGH (verified):** `main.py` does NOT pass a `price_oracle` to
   `run_snapshot_creation` (line 267). The TD-1 `liquidity_usd` support exists
   in `snapshot_job.py`, but production snapshots will be NULL-`liquidity_usd`
   until an oracle is wired. This directly fails the task criterion
   "`liquidity_usd` non-null in new snapshots."
2. **INFO (environment):** Public-RPC collector is throughput-limited
   (~2,600 blocks/hr). A real provider key (Alchemy/QuickNode, 660/50 rps)
   would remove this bottleneck for the 24h run.
3. **INFO (environment):** no `.env` provider keys — the multi-provider pool
   falls back to public RPC by design (verified).

## Verdict

- ✅ **Ingestion + normalization + persistence are verified working** against
  real Base data (facts flow; the two prior smoke-found production bugs are
  fixed).
- ❌ **Not ready to certify the full "jobs ran for 2h" success criteria** in
  this environment: the snapshot/feature/intelligence/outcome jobs were NOT
  observed over a multi-hour window, and **`liquidity_usd` is not populated in
  production because main.py doesn't wire the oracle**.
- **Blockers before a clean 24h Reality Check:** (a) add real provider keys to
  `.env` and (b) wire a price oracle into main.py's snapshot job (or decide to
  accept NULL `liquidity_usd` and document it), then run the collector for a
  sustained multi-hour window to observe the scheduler ticks.

**Next:** the genuinely missing pieces are a keyed provider run + oracle
wiring — both are deployment/human steps, not things I can correctly
fabricate in this bounded session.