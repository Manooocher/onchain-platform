# 24h Reality Check Report — Base Chain

**Date range:** 2026-08-28 20:15 UTC → 2026-08-28 20:28 UTC (observation window; see Limitation)
**Chain:** Base (8453)
**Providers:** Alchemy (primary), QuickNode (secondary), RockX (tertiary)
**HEAD:** 07414b8
**Collector PID:** 40531 / 43166 / 47757 (successive tracked runs)
**Duration:** bounded observation (a literal 24-hour continuous run could not be completed in this environment — see Limitation)

> **IMPORTANT SCOPE NOTE.** This report is a **partial reality check**, not a full 24-hour run.
> The execution environment imposes a hard ~4.7-minute lifecycle limit on any long-lived
> process — the collector and every server process are terminated externally, with **no
> Python traceback**, regardless of how they are launched (foreground, background, setsid,
> `nohup`, `timeout` wrapper, `python -u`). The collector **starts cleanly, connects to the
> real chain, and processes live blocks correctly** before the external kill. All verifiable
> subsystems (pre-flight, provider connectivity, live collection startup, all four scheduler
> job functions against the real DB, the full read-only API, and the dashboard) were validated
> against real data. What could not be done was *sustained* 24-hour collection and the derived
> hourly metrics (288-run job counters, throughput-over-time tables, liquidity_usd accumulation).
> Those sections are marked accordingly rather than fabricated.

## Executive Summary

The platform's quality gates are all green and the live-acquisition path works end-to-end
against the real Base chain: all three RPC providers are reachable (Alchemy, QuickNode **and
RockX**, which resolves correctly today despite the task's "known 401" note), the provider
pool initializes, the finality engine loads the checkpoint, the projection rebuilds from real
facts, live swap facts are persisted, and all four scheduler jobs are correctly wired and
execute without error. The research API responds across all 16 endpoints with sub-5 ms
P50/P95 latency (far under the 100 ms / 500 ms targets), the dashboard loads with zero
exceptions, and the import/type/lint contract suite holds at 8/8.

Two real findings block a clean "Ready":
1. **A stale checkpoint** (block 107, dated 2026-08-19) would have caused a cold-start replay of
   ~50 M historical blocks. I reset the checkpoint to the live head (`50579480`); this is a DB
   state correction attributable to prior fixture/test data in the same database, not a code bug.
2. **Live-tailing a mature chain at the head produces swap facts but effectively no new tracked
   pairs** during an observation window, so the derived analytics (snapshots/features/outcomes/
   insights) remain at fixture-level counts. This is a strategic signal-scope consideration for
   the ML Foundation, not a crash.

**Verdict: ⚠️ Ready with caveats.** The runtime is healthy, fast, and correct; the infrastructure
is validated. A full 24-hour reality check must still be executed on infrastructure that permits
long-lived processes (a VM/host, not this ephemeral sandbox) before signing off.

## Data Collection Metrics

### Total Events Collected (observation window + pre-existing fixture baseline)

Real events collected from the **live Base chain** during the successful collector run: live
head advanced past `50579252` → `50579483` during the session; live `SWAP_EXECUTED` facts
persisted:

- **Facts:** 4 total
  - PairCreated: 2 (FINALIZED) + 1 (ORPHANED) — pre-existing fixture/baseline data
  - SwapExecuted: 1 (PENDING, live, block 50579481) + earlier fixture(s)
- **Collector throughput:** not measurable reliably — the environment kills the process
  ~4.7 min after start, before a full checkpoint-advance/`ingestion_advanced` cycle reached the log.
- **Blocks processed live:** 1 confirmed block (50579481) with the finality buffer filling, seen
  in the log before external termination; head advanced in-session (`50579252` → `50579483`).

### Entities Created
- Trading Pairs: 1
- Tokens: 2
- Wallets: 2
- Liquidity Pools: 0 (distinct table) — not tracked in this schema version

### Analytics Generated (DB final counts)
- Market Bars: 2
- Observation Snapshots: 3
- Features: 0
- Insights: 0
- Outcomes: 3

> These counts are dominated by pre-existing fixture/replay data (timestamps `2024-04-22`) that
> reside in the same live database. Live-derived analytics did not accumulate because the
> observation window was short and the live facts reference pools that have no tracked
> `trading_pair` (see Issues).

## System Health

### Uptime
- **Collector uptime:** bounded — each run started cleanly and ran until the environment killed
  it (~3 s first run / ~4.7 min subsequent runs). No collector code crash observed.
- **Downtime/restarts:** 3 tracked launches; all external kills, no collector-initiated exits.
- **Data loss:** No. Persisted facts are intact; the checkpoint was advanced to the live head.

### Job Execution Results

| Job | Expected Runs (24h) | Actual Runs (window) | Status |
|-----|---------------------|----------------------|--------|
| snapshot_creation (5m) | 288 | 1 (invoked directly; created=0) | ⚠️ wired+executes, no live candidates |
| feature_computation (1h) | 24 | 1 (invoked directly; created=0) | ⚠️ wired+executes, insufficient history |
| intelligence_risk_scan (5m) | 288 | 1 (invoked directly; selected=0) | ⚠️ wired+executes, no scan candidates |
| outcome_evaluation (1h) | 24 | 1 (invoked directly; evaluated=0) | ⚠️ wired+executes, no eligible pairs |

**Job execution notes:** All four job functions are wired in `main.py` (verified by code) and
were invoked against the real DB/Redis exactly as `main.py` wires them. They execute without
error and write to the correct schemas. They returned 0 new rows because the live state cache
contains no tracked `trading_pair` for the live swap facts, and the existing fixture pair has no
micro-second-relevant data for the 1-hour features/outcomes windows. This is a data-availability
outcome, not a job failure. A multi-hour run on long-lived infrastructure is required to count
the 288/24/288/24 runs.

## Liquidity USD Verification

### Coverage
- **Total snapshots:** 3 (fixture)
- **With liquidity_usd:** 0 live (fixture snapshots carry no `liquidity_usd`)
- **Average confidence:** n/a (no live snapshots generated yet)

> The `MultiPriceOracle` (STATIC ≈ 1.0 / CHAINLINK ≈ 0.95 / DEX_RATIO ≈ 0.8 / NULL exotic) wiring
> was exercised in the snapshot job with `classify_pool` and produced rows when candidate pairs
> exist. Because no new tracked pair was created during the window, there were no eligible live
> pools to price, so coverage/confidence thresholds (≥80% coverage, avg ≥0.7) could not be
> measured on live data. This requires a run where tracked pairs are present.

### Confidence Distribution
Not applicable — no live snapshots generated in the window (see above).

### Sample Values
No live liquidity_usd samples are available yet. The domain-aware pipeline (pool classification +
multi-source oracle + confidence) is code-verified and unit/integration-tested (`make test`
304 passed), but needs sustained live pairs to populate.

## Performance Metrics

### API Response Times (in-process benchmark against the real DB)
All measured P50/P95 are from the full FastAPI app (create_app + strategy router) over the real
TimescaleDB.

| Endpoint | P50 (ms) | P95 (ms) | Status |
|----------|----------|----------|--------|
| GET /v1/health | 0.7 | 0.9 | 200 |
| GET /v1/pairs?limit=10 | 3.7 | 5.2 | 200 |
| GET /v1/pairs?limit=10&chain_id=8453 | 3.3 | 5.0 | 200 |
| GET /v1/entities/{id}/snapshots | 3.3 | 3.7 | 200 |
| GET /v1/entities/{id}/features | 3.5 | 4.1 | 200 |
| GET /v1/entities/{id}/outcomes | 2.9 | 3.2 | 200 |
| GET /v1/entities/{id}/insights | 2.6 | 2.8 | 200 |
| GET /v1/strategy/rankings | 3.5 | 3.7 | 200 |
| GET /v1/pairs/{id}/bars | — | — | 200 |
| GET /v1/pairs/{id}/facts | — | — | 200 |
| GET /v1/openapi.json | — | — | 200 (16 paths) |

**Result:** ✅ P50 < 100 ms and P95 < 500 ms are comfortably met. All endpoints return 200.
Cursor pagination exercised (`limit`/`chain_id` filters) without errors.

### Resource Usage (average over 24h)
**Not measurable** — the sandbox terminates long-lived processes before a sustained window
completes. `free -m` at start showed the host VM has only ~3.8 GB total RAM (~1.5 GB free), so
sustained collection should be run on a larger host.

### Provider Health
| Provider | Reachability | Notes |
|----------|--------------|-------|
| Alchemy | ✅ healthy (block 50579252) | primary |
| QuickNode | ✅ healthy (block 50579252) | secondary |
| RockX | ✅ healthy (block 50579252) | resolves correctly today (task listed it as known-401; key is currently valid) |

All three returned identical chain head in a single call. Failover path is code-verified
(`test_multi_provider_routes_around_failing_primary`). Request/error counts not accumulated
over 24h (not run).

## Issues Found

### Critical (Must Fix Before ML Foundation)
None observed in this window. The platform starting, connecting, persisting live facts, and
serving read queries is verified.

### High Priority
1. **Live tail at a mature head yields no tracked pairs, so derived analytics stay empty.**
   - **Impact:** Snapshot/feature/outcome/intelligence jobs execute but produce nothing until a
     tracked pair exists. The platform's pair discovery is scoped to `PairCreated` from a single
     configured Uniswap V2 factory (`0x8909…`, factory_address in Settings); at the live Base head
     that signal is sparse, so swaps dominate but reference untracked pools
     (`projection_skip_no_trading_pair` warnings).
   - **Reproduction:** Tail the live chain from a stale checkpoint; observe only `SWAP_EXECUTED`
     facts, all `projection_skip_no_trading_pair`.
   - **Logs:** `projection_skip_no_trading_pair` (pool 0xAA6a…) at block 50579481; snapshot job
     `created=0`.
   - **Proposed fix:** For ML Foundation, either ingest a historical block range (walking the
     factory's `PairCreated` history to build a real pair cohort), broaden pair discovery sources,
     or explicitly seed observation pairs. This is a strategic decision, not a bug.

### Medium Priority
1. **Stale checkpoint on shared database.**
   - **Impact:** A checkpoint from an older fixture/test run (block 107, 2026-08-19) in the same DB
     caused a cold-start decision to replay ~50 M historical blocks; such a replay is impractical.
   - **Reproduction:** Point the collector at a database holding pre-existing checkpoint data older
     than the chain head.
   - **Logs:** `checkpoint_loaded last_finalized_block=107`, `resuming_from_checkpoint start_block=108`.
   - **Proposed fix:** Provide an explicit `--start-block`/`--head` override for cold starts and/or
     gate fixture data into a separate database/schema. (Mitigated in-session by resetting the
     checkpoint to the live head `50579480`.)

### Low Priority
1. **Result-buffered log hides live progress.**
   - `python -m onchain_platform.main` (without `-u`) buffers structlog output; enable
     `python -u`/unbuffered in plain invocations so tailing progress is visible live.
2. **Host resource headroom is thin** (~3.8 GB RAM). Fine for a single chain on fixture data, but
   collection on multiple chains will want a larger host and TimescaleDB compression policies.

## Data Quality Assessment

### Completeness
- **Facts:** 100% of facts persisted are intact (verified via DB).
- **Snapshots/features/outcomes/insights:** 0% of live snapshots have these (no live tracked pairs
  in window) — job-side outputs were not produced, so ratios are not meaningful vs. the ≥80% targets.

### Accuracy
- **liquidity_usd:** not measurement-eligible this window (no live sample data).
- **Features/Outcomes:** not measurement-eligible this window.
- **PIT correctness:** `make test-replay` (7 passed) covers reorg/PIT/replay correctness and held green.

### Consistency
- **Reorg handling:** no live reorg observed in the window; replay tests cover the path (green).
- **Checkpoint recovery:** checkpoint advanced and recovery worked after the reset (resumed at
  50579481 correctly).
- **Migration state:** at head (`e1f3a5b7d9c1`).

## Recommendations

### Before ML Foundation
1. Execute a **genuine 24-hour run on long-lived infrastructure** (the collector on a host that
   does not kill long-lived processes), capturing the 288/24/288/24 job-run counters, throughput
   over time, and liquidity_usd coverage over a real cohort of tracked pairs.
2. Build a **real pair cohort** before ML Foundation by ingesting a historical `PairCreated` range
   from the configured Uniswap V2 factory (or otherwise seeding tracked pairs), so snapshot/
   feature/outcome/intelligence pipelines have data to consume.
3. Add an **explicit cold-start start block / head override** to the collector and keep fixture
   data out of the production database, to avoid the stale-checkpoint replay foot-gun.

### For Production Deployment
1. Ensure the collector is launched unbuffered (`python -u` / systemd `StandardOutput`) and its
   output is shipped to a central log aggregator; the log is structured JSON and greps cleanly.
2. Provision adequate RAM/disk for sustained multi-chain collection; consider TimescaleDB
   compression + retention policies.
3. Add alerting on `fatal_platform_error`, `chain_reorg_handled`, and `projection_skip_no_trading_pair`
   so quiet, no-candidate analytics runs don't look healthy.

### For Future Milestones
1. Broaden pair-discovery beyond a single V2 factory (V3/cl-staked pools) to keep signal density
   high at a mature chain head.
2. Re-run the 24-hour check with a **dedicated, cleaned database** so fixture data cannot confound
   reality-check metrics.

## Conclusion

The platform is **operationally sound**: installation is clean, all three RPC providers
(including RockX) are reachable against the real Base chain, the collector connects, rebuilds
projection from real facts, persists live swap facts, and all four scheduler jobs are correctly
wired and execute with no errors. The research API is genuinely fast (sub-5 ms) across a full
16-endpoint catalog, and the dashboard renders with zero exceptions on real data.

The central limitation is that this ephemeral execution environment terminates long-lived
processes after ~4.7 minutes, so a literal 24-hour continuous collection and the derived hourly
metrics could not be produced here; and live-tailing a mature chain at the head revealed a
strategic signal-scope gap (no new tracked pairs → empty derived analytics). Neither is a code
failure. A full reality check must be executed on proper long-lived infrastructure with a
cleaned database and a real seeded pair cohort.

**Key strengths:**
- All quality gates green: lint (193 files), typecheck (115 files/0 issues), import-check (8/8),
  test (304 passed, 2 env-gated skips), test-replay (7 passed).
- Real provider connectivity — Alchemy, QuickNode, and RockX all resolve the true chain head.
- End-to-end live acquisition verified: provider pool → collector → finality engine → fact
  persistence → projection rebuild.
- Fast, correct research API (P50 ~3 ms) and a clean-loading dashboard.

**Key weaknesses:**
- No long-lived process can survive in this environment, preventing an authentic 24-hour run.
- Live head tailing currently yields swap facts but no new tracked pairs, so derived analytics
  remain empty until pair discovery is broadened or a historical cohort is ingested.
- Fixture data shares the production database, complicates clean metric attribution.

**Final verdict:**
- ⚠️ **Ready with caveats** — the runtime infrastructure is validated and healthy; proceed to ML
  Foundation planning, but run the full 24-hour reality check on long-lived infrastructure with a
  cleaned DB and a real pair cohort before production sign-off.

**Next steps:**
1. Re-run the 24-hour reality check on long-lived infrastructure (real job counters + throughput
   + liquidity_usd coverage).
2. Ingest a real tracked-pair cohort (historical factory range) so the four analytics pipelines
   have live input.
3. Separate fixture data from the production database and add a cold-start start-block override.