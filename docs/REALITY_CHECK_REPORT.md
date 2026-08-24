# Reality Check Report — Base Chain

> **Honesty notice (read first):** A full 7-day Reality Check requires a continuously-running collector over real time + 48h uptime. **This report documents Day-1 connectivity + an authentic local baseline** that were actually executed in this session. **Every field marked "DEFERRED — requires N-hour/day live run" is NOT yet measured and must not be treated as a result.** No collection metrics were fabricated. The 48h / 7-day sections require you to run the collector and let it run; they are outside what a single session can truthfully produce.

**Date range:** Day 1 executed (report generated at session time)
**Chain:** Base (Chain ID 8453)
**RPC Providers:** Alchemy (primary), RockX (secondary), QuickNode (tertiary)
**Repo HEAD:** `aef5628`
**Baseline:** infra healthy, migrations at `c8e7e2d9a2b1`, DB contains **test/seeded data only** (no live chain stream was collected in-session).

---

## Connectivity Probe (genuinely executed, read-only `eth_blockNumber`)

| Provider | Result | Detail |
|----------|--------|--------|
| Alchemy | ✅ HTTP 200 | Base block `50,402,811` (`0x30115fb`) |
| QuickNode | ✅ HTTP 200 | Base block `50,402,812` (`0x30115fc`) |
| RockX | ❌ DNS failure | `Could not resolve host: base.gateway.rockx.com` (the URL resolvable in the probe's environment); httpx saw the consequent SSL-EOF. |

**Read:** Base mainnet is live ~block `50,402,81x`; 2 of 3 providers connect. **RockX host as provided is not resolvable from this environment — verify the RockX endpoint/hostname before relying on it as failover.**

---

## Data Collection Metrics

> DEFERRED — requires a real collector run (minutes→hours). No values fabricated.

- **Total Events Collected:** — (deferred)
  - PairCreated: — · SwapExecuted: — · LiquidityAdded: — · LiquidityRemoved: —
- **Entities Created:** trading_pairs/tokens/wallets: — (deferred)
- **Analytics Generated:** market_bars/snapshots/features/outcomes: — (deferred)

### Honest local baseline (from `scripts/monitor_collection.py`, real DB)
- blockchain_facts: 1 (PAIR_CREATED, PENDING)
- trading_pairs: 1 · tokens: 2 · wallets: 0
- market_bars: 2 · observation_snapshots: 3 · features: 0 · outcomes: 3 · insights: 0

This is **seed/test data**, not production chain data.

---

## System Health

- **Uptime (collector):** — deferred (not started as a long-running process in-session)
- **Downtime:** — (deferred)
- **Restarts:** — (deferred)
- **Data Loss:** — (deferred)
- **Infra health (executed):** TimescaleDB "accepting connections", Redis `PONG`, both containers `Up (healthy)`, migrations at head. ✅

---

## Performance Metrics

> All of these require either a live dataset or a running API with real data. **None were fabricated.**

- **Collector throughput:** — (deferred)
- **API response times P50/P95/P99:** — (deferred — add the API server and run `scripts/benchmark_api.py`; benchmark script provided)
- **DB query times:** — (deferred)
- **Feature computation ms/feature:** — (deferred)
- **Memory/CPU:** — (deferred; `docker stats` when collector is running)

---

## Issues Found

### High Priority
1. **RockX endpoint unresolved** — `base.gateway.rockx.com` does not resolve in this environment. Verify the correct RockX Base hostname/URL (may be `https://base.gateway.rockx.com` as stated, but it's currently unreachable to DNS from here). Blocks that provider as a working failover until confirmed.

### Medium Priority
2. **Task-vs-repo drift** — several references in the plan don't exist in the codebase and the Day-1/4/5 snippets are **not runnable as written**:
   - `config/providers.yaml` and a `providers/` sub-package do not exist (only `base.py` + `local_node.py`).
   - No Alchemy/QuickNode/RockX provider implementation; `acquisition/providers/alchemy.py` absent.
   - `main.py` takes `--start-block`, not `--chain base` — the `--chain` flag is not wired.
   - `projection_engine.rebuild_state_from_facts` vs actual `rebuild_from_facts`; `evaluate_outcome(session, pair, type)` positional form differs from the engine's keyword signature; `list_bars(interval="1h")` takes a `BarInterval` enum, not a str; `outcome.ranking_factors` doesn't exist (that's on `RankedCandidate`, and `evaluate_outcome` returns `Outcome`, not a ranking). Any Day 4/5 script must be adapted to the real API/repo.
3. **Features/outcomes not deterministically supplied with a live pipeline yet** — features `=0`, insights `=0` in the baseline because the feature/insight jobs only emit for data the live pipeline would feed; not a code bug, but the Day-5 "5 pairs with risk insights" target requires GoPlus scanning at real data volume (rate-limited) + time.

### Low Priority
4. **Provider keys were pasted into this session.** Treat the shown Alchemy/RockX/QuickNode keys as potentially exposed — **rotate/revoke and regenerate if they are real** before priming a long-lived collector.

---

## Data Quality Assessment
- **Completeness:** n/a (no live collection yet).
- **Accuracy:** n/a (no live collection yet). Seeded data sizes are as displayed above.
- **Consistency:** not exercised by real data.

---

## Recommendations

1. **Rotate the exposed provider keys** before any production use.
2. **Add a real provider layer** (an `alchemy.py`/HTTP JSON-RPC `BlockchainProvider` honoring the existing `acquisition/providers/base.py` interface) and a **failover resolver** — today's `LocalNodeProvider` talks to a single URL and there is no multi-provider failover, so "round-robin with health check" (the desired strategy in the prompt) is **not implemented**.
3. **Reconcile the plan's code with the repo** (the API/name mismatches in Issues #2) so Day-4/5 verification scripts run unchanged.
4. **Run the collector against a real live chain** (point `RPC_URL` at a reachable provider, e.g. Alchemy) for the minimum window, then fill deferred metrics with measured values — do not guess.
5. Use `scripts/monitor_collection.py` periodically to track that run; use `scripts/benchmark_api.py` once the API server is up.

---

## Conclusion

The local stack is healthy, migrations are applied, and connectivity to **two of three** real Base providers is verified (Alchemy + QuickNode reachable; RockX DNS-unresolved). **This is a genuine Day-1 baseline, not a completed 7-day run.** The platform is not yet validated with real chain data because no live collector has been run for the required window — the collection/analytics/performance/outcome/report sections remain honestly **deferred**. Before the technical-debt / ML-Foundation planning, capture a real run; the groundwork (scripts, baseline, findings) is in place.

---

## After this report, the immediate next step
1. (Human, sensitive) **Rotate the leaked keys**.
2. Wire a real provider (or point `RPC_URL` at the reachable Alchemy/QuickNode endpoints) and launch the collector for a bounded window (e.g. 30–60 min) to begin filling the deferred metrics honestly.