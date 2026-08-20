# Milestone 3 Execution Plan — SwapExecuted & Market Bars

Status: Planning artifact. Implements `docs/implementation/ImplementationPlan.md` § Milestone 3.
Prepared: 2026-08-20, after re-reading ImplementationPlan § Milestone 3, DOC-012 § B.1 (SwapExecuted
payload), DOC-012 § B.3 (MarketBar), DOC-014 § TimescaleDB Hypertables / § Type Mapping Rules /
§ Indexing Strategy, DOC-008 § Financial Precision Principle, DOC-006 § Market Data Pipeline.

Goal (verbatim from ImplementationPlan): real swaps become real Decimal-precise facts, and a real
OHLCV bar is correct and reproducible.

---

## 0. Pre-Flight Status

Verified against HEAD 5a4b0b5 (commands run, not assumed):

| Item | Status | Detail |
|---|---|---|
| M2 gates pass | ✅ Done | ruff clean, mypy strict 58 files 0 issues, lint-imports 8/8, 43 fast + 4 replay + 1 live smoke |
| SwapExecutedPayload exists in discriminated union | ✅ Done | `blockchain_fact.py` line 206: `SwapExecutedPayload` is in the union. Class defined at line 107 with all DOC-012 fields + validators. |
| Normalizer only decodes PairCreated | ✅ Confirmed | `normalizer.py` has `PAIR_CREATED_TOPIC` and `normalize_pair_created()` only. No Swap decoding. |
| persistence/timescale/ is empty | ✅ Confirmed | Only `__init__.py`. No hypertables, no repositories. |
| analytics/trade_aggregator.py does not exist | ✅ Confirmed | `ls` → No such file |
| Replay fixtures contain zero SwapExecuted events | ✅ Confirmed | `grep SWAP_EXECUTED tests/replay/fixtures/` → no matches |
| V2 Swap topic verified live | ✅ Done | `0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822` — confirmed via eth_utils keccak. 177 Swap events found in blocks 13,500,004–13,500,024 (same range as existing PairCreated fixture). |

**Pre-flight summary:** all M2 artifacts in place, all M3 prerequisites absent as expected. The
SwapExecutedPayload class and its validators already exist (typed from DOC-012 in M1) — M3 only
needs to wire the normalizer and fact_processor to actually use them.

---

## 1. Open Decisions — Resolved

| Decision | Resolution | Rationale |
|---|---|---|
| Target DEX for SwapExecuted on Base | **Uniswap V2 factory on Base** (`0x8909dc15e40173ff4699343b6eb8132c65e18ec6`) — same factory as M1. The V2 Swap event signature (`0xd78ad95f...`) is identical across all V2 forks. Aerodrome (Solidly-fork) uses a different Swap signature and is explicitly deferred (DOC-012 § Known future extension). | M1 already verified this factory live. The Swap ABI is the same as mainnet Uniswap V2. No new factory-specific logic needed. |
| Bar bucketing alignment | **Epoch-based modulo arithmetic:** `bar_start = event_time - (event_time % interval_seconds)`, where `interval_seconds` is `{1m: 60, 5m: 300, 15m: 900, 1h: 3600}`. `bar_end = bar_start + interval_seconds`. Strict `bar_start <= event_time < bar_end` predicate (no inclusive upper bound — DOC-012 § B.3 reconstruction predicate). | Deterministic, timezone-independent (UTC epoch arithmetic), no ambiguity at boundaries. The `event_time` is the block timestamp (DOC-008 Triple Timestamp Standard) — already UTC-aware. |
| `is_provisional` bar generation | **FINALIZED only for M3.** `is_provisional` is always `False`. Provisional bar generation (from CONFIRMED facts) is deferred — it's a dashboard optimization (DOC-007) that doesn't affect the research correctness bar. The `is_provisional` field exists in the schema (DOC-012 § B.3) and is set to `False`; the infrastructure to generate provisional bars arrives when the dashboard milestone needs it. | Keeps M3 scoped to the Financial Precision proof. Provisional bars add complexity (dual query paths, reorg handling for provisional bars) with no research value. |
| Replay fixture selection for Swaps | **Extend the existing fixture** (blocks 13,500,004–13,500,024) to include Swap events. Live probe found 177 Swap events in this range. Select 5–10 events from 2–3 different pools to test multi-pair aggregation. The fixture already has PairCreated events — adding Swap events makes it a complete M1+M2+M3 fixture. | Reuses committed fixture infrastructure. The block range is already deep-finalized. Multiple pools test the pair_id routing logic in the trade aggregator. |

---

## 2. Build Order (Sequential)

Gates: every step passes `make lint && make typecheck && make import-check` before the next begins.

### Phase A: Normalizer + Fact Processor (Swap decoding)

1. **`src/onchain_platform/processing/normalizer.py`** — add `SWAP_TOPIC` constant and `normalize_swap()` function.
   - Purpose: decode V2 Swap event (`Swap(address indexed sender, uint256 amount0In, uint256 amount1In, uint256 amount0Out, uint256 amount1Out, address indexed to)`). Topics: `[SWAP_TOPIC, sender, to]`. Data: 4 × 32-byte words = amount0In, amount1In, amount0Out, amount1Out.
   - All amounts converted to decimal strings (int(hex) → str). Never float. DOC-008 § Financial Precision Principle: "Floating-point numbers are prohibited."
   - Addresses EIP-55 checksummed (eth_utils, same as PairCreated).
   - Validation: at least one `_in` > 0 AND at least one `_out` > 0 (a swap with all zeros is nonsensical). Also: `amount0_in > 0 XOR amount1_in > 0` (V2 swaps are one-directional — you don't send both tokens in).
   - `pool_address` comes from `raw.address` (the emitting contract), not from topics.
   - Returns `NormalizedSwapEvent` dataclass (frozen, same pattern as `NormalizedPairCreatedEvent`).
   - Deps: existing normalizer.py (M1).
   - Verification: unit test against the real captured Swap log (block 13,500,004, logIndex 22, pool `0x39f0e675d479088de08b7f201ac08e20f899b838`, amount0_in=0, amount1_in=34099401194346, amount0_out=14339668586465206, amount1_out=0 — verified live during planning).
   - Complexity: moderate.

2. **`src/onchain_platform/processing/fact_processor.py`** — extend `process()` to handle `SWAP_EXECUTED`.
   - Purpose: dispatch on `topics[0]` — if `PAIR_CREATED_TOPIC`, existing path; if `SWAP_TOPIC`, new path that calls `normalize_swap()` and constructs `SwapExecutedPayload`.
   - The fact_type↔payload.fact_type sync enforcement (DOC-012 § Modeling note) applies to both paths.
   - Deps: step 1.
   - Verification: unit test producing the exact expected `BlockchainFact` for the captured Swap log. All amount fields are `str` (zero-tolerance byte-identity). No float anywhere.
   - Complexity: moderate.

3. **`tests/unit/test_pipeline_swap.py`** — unit tests for the Swap normalizer + fact processor.
   - Real captured Swap log (block 13,500,004, logIndex 22) as test fixture.
   - Assert: all amount fields are `str`, byte-identical to expected values.
   - Assert: addresses are EIP-55 checksummed.
   - Assert: rejected if all amounts are zero.
   - Assert: rejected if both `_in` fields are > 0 (invalid V2 swap).
   - Deps: steps 1, 2.
   - Verification: `make test` green.
   - Complexity: moderate.

### Phase B: Collector extension (multi-filter)

4. **`src/onchain_platform/acquisition/collector.py`** — extend to support multiple log filters.
   - Purpose: the current collector accepts one `(factory_address, event_topic)` pair. M3 needs to also collect Swap events (from any pool address, filtered by `SWAP_TOPIC`). Extend the constructor to accept `filters: list[tuple[str | None, str]]` — each tuple is `(address_or_None, topic)`. When `address` is `None`, the provider returns logs matching the topic from any address.
   - Backward-compatible: the old `factory_address` + `event_topic` parameters become a single filter entry. M1/M2 callers pass one filter; M3 passes two.
   - `_process_block` iterates over all filters, collects logs from each, merges and sorts by `(block_number, log_index)` (DOC-013 § Determinism Discipline — ordered iteration only).
   - `dex` attribution: each filter carries its own `dex` label. For Swap events, the `dex` comes from the filter configuration (not from the log — the log's `address` is the pool, not the factory).
   - Deps: existing collector.py (M2).
   - Verification: existing M1/M2 tests still pass (backward-compatible). New unit test with two filters asserting correct merge order.
   - Complexity: moderate.

5. **`src/onchain_platform/main.py`** — wire the second filter for Swap events.
   - Add a second filter entry: `(None, SWAP_TOPIC, "uniswap_v2")` — no address filter (Swap events come from any pool), topic = Swap signature, dex = "uniswap_v2".
   - Deps: step 4.
   - Verification: `make lint`, `make typecheck`, `make import-check` pass.
   - Complexity: trivial.

### Phase C: MarketBar schema + TimescaleDB hypertable

6. **`src/onchain_platform/domain/schemas/market_bar.py`** — `MarketBar` schema (DOC-012 § B.3).
   - Fields per DOC-012: `schema_version`, `bar_id` (`f"{pair_id}|{interval}|{bar_start_time.isoformat()}"` — `|` delimiter per DOC-012 § Composite ID Delimiter), `pair_id`, `chain_id`, `interval` (enum: 1m/5m/15m/1h), `bar_start_time`, `bar_end_time`, `open`/`high`/`low`/`close` (str — Decimal-as-string), `volume_base`/`volume_quote` (str), `trade_count` (int), `vwap` (str), `buy_volume`/`sell_volume` (str), `source_fact_range` (tuple[str, str]), `is_provisional` (bool), `computed_at` (datetime).
   - Frozen model per DOC-013.
   - New enum: `BarInterval` (1m/5m/15m/1h) — goes in `domain/schemas/enums.py`.
   - Validators: `bar_start_time < bar_end_time`, all OHLCV fields are non-negative decimal strings, `source_fact_range[0] <= source_fact_range[1]` (lexicographic — but note DOC-014's warning: this is for audit, not for chronological range queries).
   - Deps: none.
   - Verification: unit test round-trip, frozen-mutation rejection.
   - Complexity: moderate.

7. **`src/onchain_platform/persistence/timescale/repositories.py`** — `market_bars` hypertable + CRUD.
   - ORM model `MarketBarRow` per DOC-014: all OHLCV columns are `NUMERIC` (unconstrained precision — DOC-014 § Type Mapping Rules, "Price / Ratio / Derived-USD" category). `trade_count` is `INTEGER`. `source_fact_range_start`/`source_fact_range_end` are `TEXT` (DOC-014 § Standard mappings — tuple stored as two columns). `bar_start_time`/`bar_end_time`/`computed_at` are `TIMESTAMPTZ`.
   - Hypertable: partitioned by `bar_start_time`, chunk interval 7 days (DOC-014 § TimescaleDB Hypertables). Compression policy: compress chunks older than 30 days.
   - Index: `(pair_id, interval, bar_start_time DESC)` — DOC-014 § Indexing Strategy: "The primary research query: OHLCV history for one pair, one interval, over a time range."
   - `CHECK` constraints: `volume_base >= 0`, `volume_quote >= 0`, `buy_volume >= 0`, `sell_volume >= 0`, `trade_count >= 0` (DOC-014 § Data Integrity Constraints).
   - CRUD: `save_bar(session, bar)` — upsert (INSERT ON CONFLICT UPDATE for recomputation on reorg). `list_bars(session, pair_id, interval, from_time, to_time)` — ordered by `bar_start_time`.
   - Deps: step 6.
   - Verification: integration test against real TimescaleDB (compose). Insert bar, read back, assert fields. Verify hypertable creation via `SELECT * FROM timescaledb_information.hypertables`.
   - Complexity: high (TimescaleDB hypertable + compression policy + Alembic migration).

8. **Alembic migration** — `market_bars` hypertable creation.
   - Hand-written (same rationale as M2's blockchain_facts migration: autogenerate can't handle hypertables).
   - Creates the table, then `SELECT create_hypertable('market_bars', 'bar_start_time', chunk_interval => INTERVAL '7 days')`.
   - Adds compression policy: `SELECT add_compression_policy('market_bars', INTERVAL '30 days')`.
   - Creates the index `(pair_id, interval, bar_start_time DESC)`.
   - Deps: step 7.
   - Verification: `make migrate` on fresh container; `\d market_bars` shows correct schema; `SELECT * FROM timescaledb_information.hypertables` shows the hypertable.
   - Complexity: moderate.

### Phase D: Trade Aggregator — the core artifact

9. **`src/onchain_platform/analytics/trade_aggregator.py`** — Market Bar generation.
   - Purpose: queries `FINALIZED` `SWAP_EXECUTED` facts for a given `pair_id` and time window, computes OHLCV + VWAP + trade count + buy/sell volume. **All intermediate math uses `Decimal`.** A single `float` conversion invalidates the entire milestone.
   - `pair_id` construction: `f"eip155:{chain_id}/pair:{pool_address}"` — deterministic, no entity resolution needed (M4).
   - Bar bucketing: `bar_start = event_time - (event_time % interval_seconds)`. The `event_time` is the block timestamp (UTC-aware). Epoch arithmetic is timezone-independent.
   - Price calculation: for each swap, determine direction:
     - If `amount0_in > 0` (selling token0): `price = Decimal(amount1_out) / Decimal(amount0_in)` (token1 per token0).
     - If `amount1_in > 0` (buying token0): `price = Decimal(amount1_in) / Decimal(amount0_out)` (token1 per token0).
     - Division uses `Decimal` with sufficient precision (Python's `Decimal` division is exact for rational numbers; set context precision high if needed).
   - OHLCV: `open` = first price in bar, `high` = max price, `low` = min price, `close` = last price. All stored as `str(Decimal)`.
   - Volume: `volume_base` = sum of token0 amounts traded (amount0_in + amount0_out across all swaps). `volume_quote` = sum of token1 amounts traded. `buy_volume` = token0 bought (amount0_out when amount1_in > 0). `sell_volume` = token0 sold (amount0_in when amount1_out > 0).
   - VWAP: `sum(price_i * volume_base_i) / sum(volume_base_i)` — all `Decimal`.
   - `source_fact_range`: `(first_fact_id, last_fact_id)` of the facts that composed the bar, ordered by `(block_number, log_index)`. This is for audit, not for reconstruction (DOC-012 § B.3: "the reconstruction predicate, not the source_fact_range bounds alone, is the authoritative definition").
   - `is_provisional = False` for M3.
   - Reconstruction predicate (DOC-012 § B.3): `SELECT * FROM blockchain_facts WHERE chain_id = :chain_id AND fact_type = 'SWAP_EXECUTED' AND confirmation_status = 'FINALIZED' AND payload->>'pool_address' = :pool_address AND event_time >= :bar_start AND event_time < :bar_end ORDER BY block_number, log_index`.
   - On reorg: if any fact in `source_fact_range` transitions to ORPHANED, the bar is fully recomputed from the predicate — never patched incrementally (DOC-012 § B.3 explicit rule).
   - Deps: steps 6, 7.
   - Verification: unit test with synthetic swap sequence → exact OHLCV values (Decimal zero-tolerance). Integration test against real Postgres.
   - Complexity: **high** (the highest correctness bar in M3 — Decimal math, bar boundary edge cases, reorg recomputation).

10. **`tests/unit/test_trade_aggregator.py`** — unit tests for the trade aggregator.
    - Synthetic swap sequence: 3 swaps in the same 1-minute bar, different prices. Assert exact OHLCV, VWAP, trade count, buy/sell volume. All `str` comparisons, zero tolerance.
    - Bar boundary edge case: swap at exactly `bar_start_time` (included), swap at exactly `bar_end_time` (excluded — strict `start <= event_time < end`).
    - Multi-pair test: swaps from two different pools in the same time window → two separate bars, no cross-contamination.
    - Empty bar: no swaps in a time window → no bar produced.
    - Deps: step 9.
    - Verification: `make test` green.
    - Complexity: high.

### Phase E: Replay fixture + integration tests

11. **`scripts/fetch_replay_fixture.py`** — extend to also fetch Swap events.
    - Add `SWAP_TOPIC` to the fetch script's topic filter. Fetch Swap events from the same block range (13,500,004–13,500,024). Select 5–10 events from 2–3 different pools.
    - Update the fixture JSON to include Swap logs alongside PairCreated logs.
    - Deps: step 1 (Swap topic constant).
    - Verification: fixture committed, contains both PairCreated and Swap events.
    - Complexity: trivial.

12. **`tests/replay/test_replay.py`** — extend with Swap replay test.
    - New test: `test_replay_swap_executed_produces_byte_identical_facts`. Replay the extended fixture through the live pipeline (collector → normalizer → fact processor → persistence). Assert: Swap facts are byte-identical to frozen expected values. All amount fields are `str`, zero tolerance.
    - Deps: steps 2, 4, 11.
    - Verification: `make test-replay` green.
    - Complexity: moderate.

13. **`tests/integration/test_market_bars.py`** — integration tests for Market Bar generation.
    - `test_swap_sequence_produces_correct_ohlcv`: insert FINALIZED SwapExecuted facts (via repository), run trade aggregator, assert exact OHLCV values against frozen expected values. All `str` comparisons, zero tolerance.
    - `test_bar_recomputation_on_reorg`: insert facts, compute bar, orphan one fact (via `mark_facts_orphaned`), recompute bar, verify the bar changed (trade count decreased, volumes changed, OHLCV updated). The bar is fully recomputed from the predicate, never patched (DOC-012 § B.3 explicit rule).
    - `test_bar_boundary_edge_cases`: swap at exactly `bar_start_time` (included in bar), swap at exactly `bar_end_time` (excluded — goes into next bar).
    - Deps: steps 7, 9.
    - Verification: `make test` green with compose up.
    - Complexity: high.

14. **`tests/replay/test_replay.py`** — extend with Market Bar replay test.
    - New test: `test_replay_market_bar_byte_identical`. Run the extended fixture through the full pipeline (collector → normalizer → fact processor → finality engine → trade aggregator → persistence). Assert: Market Bars are byte-identical to frozen expected values. All OHLCV fields are `str`, zero tolerance.
    - This is the DoD item: "A known historical swap sequence produces byte-identical OHLCV values on replay."
    - Deps: steps 9, 12.
    - Verification: `make test-replay` green.
    - Complexity: high.

### Phase F: Final gate

15. **Final gate + commit.**
    - `make lint && make typecheck && make import-check && make test && make test-replay` all green.
    - Update ImplementationPlan.md Milestone 3 DoD checkboxes.
    - Commit.

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `Decimal` precision loss during VWAP calculation | Medium | High | Python's `Decimal` division is exact for rational numbers within the context precision. Set `decimal.getcontext().prec = 78` (matching NUMERIC(78,0) for uint256 inputs) in the trade aggregator. Never convert to `float` at any point. Unit test asserts exact `str(Decimal)` values, not approximate. |
| Bar boundary edge cases (swap exactly at `bar_end_time`) | Medium | Medium | Strict `bar_start <= event_time < bar_end` predicate (DOC-012 § B.3 reconstruction predicate). Unit test with a swap at exactly `bar_end_time` asserts it goes into the next bar, not the current one. |
| V2 vs V3 Swap signature confusion | Low | High | Normalizer checks `topics[0] == SWAP_TOPIC` (the V2 signature). V3 Swap has a different signature (`0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67`). The normalizer rejects unknown topics with `DomainValidationError`. No V3 code is written in M3. |
| TimescaleDB hypertable creation in Alembic | Medium | High | Hand-written migration (same pattern as M2's blockchain_facts). Verify against fresh container: `docker compose down -v && make run && make migrate`. Check `timescaledb_information.hypertables` and compression policy. |
| `source_fact_range` lexicographical ordering fallacy | Low | High | DOC-014 § Standard mappings explicitly warns: `fact_id` contains `tx_hash` which carries no chronological ordering. The aggregator uses `(block_number, log_index)` for ordering, not `fact_id` lexicographic comparison. `source_fact_range` records what the predicate matched, for audit only. |
| Collector multi-filter merge order non-deterministic | Low | Medium | `_process_block` merges logs from all filters and sorts by `(block_number, log_index)` — deterministic regardless of provider return order (DOC-013 § Determinism Discipline). |
| Swap with both `_in` fields > 0 (invalid V2 swap) | Low | Medium | Normalizer validates: `amount0_in > 0 XOR amount1_in > 0`. Rejects with `DomainValidationError`. This is defense-in-depth — V2 pools enforce this at the contract level. |
| Division by zero in VWAP when volume_base = 0 | Low | Medium | A swap with `amount0_in = 0` and `amount0_out = 0` is impossible in V2 (the pool always transfers tokens). But defensively: if `volume_base = 0` for a bar, set `vwap = "0"`. Unit test covers this edge case. |
| Bar recomputation on reorg doesn't re-query the DB | Medium | High | The trade aggregator's `recompute_bar()` method re-runs the reconstruction predicate against the DB (not an in-memory cache). This ensures it picks up the current FINALIZED facts, including any that were just orphaned. Unit test: insert facts → compute bar → orphan one fact → recompute bar → verify changed. |

---

## 4. Definition of Done Matrix

| DoD Item (ImplementationPlan § Milestone 3) | Verification Method | Automated? |
|---|---|---|
| Known historical swap sequence produces byte-identical OHLCV on replay | `test_replay.py::test_replay_market_bar_byte_identical` — replay fixture through full pipeline, assert OHLCV fields are `str`, zero tolerance | Yes |
| Bar whose underlying facts include an orphan is fully recomputed, never patched | `test_market_bars.py::test_bar_recomputation_on_reorg` — insert facts, compute bar, orphan one fact, recompute bar, verify changed | Yes |
| `lint-imports` still passes | `make import-check` | Yes |
| Financial Precision Principle strictly enforced | `mypy` strict + schema tests asserting `str` type for all amount fields + unit test asserting no `float` in Swap payload | Yes |
| Swap facts are Decimal-precise from parse time | `test_pipeline_swap.py` — captured real Swap log → `BlockchainFact`, all amount fields are `str`, byte-identical | Yes |
| Bar bucketing is deterministic and timezone-independent | `test_trade_aggregator.py` — synthetic swaps at boundary timestamps → exact bar_start_time values | Yes |
| Multi-pair aggregation produces separate bars | `test_trade_aggregator.py` — two pools in same time window → two bars, no cross-contamination | Yes |

---

## 5. Out-of-Scope Confirmation

Per ImplementationPlan § Milestone 3 and § What Not To Build Yet:

- [x] Uniswap V3 / concentrated liquidity — NOT implemented. DOC-012 § Known future extension explicitly defers V3 `Mint`/`Burn`. The normalizer only decodes V2 Swap signature.
- [x] Feature Engineering — NOT built. `analytics/feature_engine.py` not created (Milestone 6).
- [x] State Projection / Observation Snapshots — NOT built. `analytics/projection_engine.py` not created (Milestone 5).
- [x] Domain Management / Entity Resolution — NOT built. `domain_management/` stays empty (Milestone 4). `pair_id` is constructed from `pool_address` + `chain_id` without entity resolution.
- [x] Provisional bar generation — NOT built. `is_provisional = False` always. Deferred to dashboard milestone.
- [x] API endpoints — NOT built. `research/` stays empty (Milestone 9).
- [x] Redis Streams — NOT introduced. Direct function call pattern continues.
- [x] Second chain beyond Base — Base remains the development chain.
- [x] `BEFORE UPDATE` trigger for FINALIZED-immutability — application-level guard sufficient per DOC-014.
- [x] No `utils/` or `common/` package introduced.
- [x] No UUID fact IDs — natural composite keys continue.

---

## 6. Questions / Blockers

Q1 (needs human): The trade aggregator needs to query `SWAP_EXECUTED` facts by `pool_address` (extracted from `payload->>'pool_address'`). The current `list_facts_for_chain()` function queries by `chain_id` only. M3 needs a new query function: `list_finalized_swap_facts(session, chain_id, pool_address, from_time, to_time)`. This goes in `persistence/postgres/repositories.py`. Confirm this is acceptable — it's a new query path, not a change to existing functions.

Q2 (design note, flagged per Document Resolution Protocol): The `pair_id` for Market Bars is constructed as `f"eip155:{chain_id}/pair:{pool_address}"` — this is the Canonical ID format from DOC-008. However, without entity resolution (Milestone 4), there's no `TradingPair` row in the database. The `pair_id` is a string identifier used only in Market Bars; it doesn't reference a foreign key. When M4 lands, the `TradingPair.canonical_id` will match this format by construction. Flagging so it's documented, not surprising.

Q3 (needs human): The existing fixture (blocks 13,500,004–13,500,024) has 177 Swap events. For the replay fixture, I recommend selecting 5–10 events from 2–3 different pools to keep the fixture small but test multi-pair aggregation. The specific events would be selected at build time based on pool diversity. Confirm this approach.

Q4 (doc gap, not a blocker): DOC-012 § B.3 defines `source_fact_range` as `tuple[str, str]`. DOC-014 stores it as two columns (`source_fact_range_start TEXT`, `source_fact_range_end TEXT`). The `MarketBar` schema uses a tuple; the ORM model uses two columns. The repositories translation boundary handles the conversion. This is consistent with how M1/M2 handle other type differences — flagging so it's documented.

Q5 (design note): The trade aggregator's `recompute_bar()` method needs to know which bars are affected by a reorg. The finality engine currently calls `ReorgEventHandler.handle_reorg(event)` with the orphaned block range. M3 needs a mechanism to translate "orphaned facts in block range [X, Y]" to "recompute bars whose time window overlaps with the event_time range of those facts." This is a query against `blockchain_facts` (get the min/max `event_time` of orphaned facts in the range, then recompute all bars whose `bar_start_time`/`bar_end_time` overlaps). This logic goes in the trade aggregator or a thin orchestrator. Not a blocker — the query is straightforward.

No hard blockers: every Q above has a stated fallback that keeps M3 buildable today.
