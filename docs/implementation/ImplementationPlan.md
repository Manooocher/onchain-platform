---
title: Implementation Plan — Where We Are, Where To Start
status: Living document — update as milestones close, not as a new DOC-xxx
last_updated: 2026-08-19
scope: This file sits outside the DOC-001…015 / ADR-006 series on purpose. Those documents define what the platform is. This one answers a single, narrower question — what do you actually open first, in what order, and how do you know each step is really done.
---

# Implementation Plan

> Fifteen documents and one ADR answered "what are we building and why." This one answers "what do I do Monday morning."

---

# Where We Actually Are

This is not a hopeful status update — it's a specific claim, checkable against the document set itself:

- **The domain is fully specified.** Every entity, every temporal concept, every field, every type — DOC-006, DOC-008, DOC-012 — down to the exact Postgres column type (DOC-014) and the exact wire format (DOC-015).
- **The hard architectural questions are already answered, not deferred.** Reorg handling has a real design (ADR-006's header-buffer + Confirmation Lifecycle). Point-in-Time correctness has a real query pattern (DOC-014's index, DOC-015's endpoint), not just a slogan. The Decimal-vs-float boundary is resolved with a stated test, not a vibe. Composite ID collisions were found and fixed before a single line of code depended on them.
- **The module boundaries are enforced, not just drawn.** DOC-011's `import-linter` contracts are config, not prose — they fail a build the moment `strategy/` reaches somewhere it shouldn't.
- **The building blocks are unusually well-rehearsed for having never been built.** Several of the decisions above went through more than one round of real scrutiny before anyone wrote implementation code against them — that is the expensive part of a project like this, and it is already paid for.

Phase 0 (DOC-005's own name for this stage) is done. What follows is not "more planning" — it's Phase 1, Market Observation, and it starts with code.

---

# The One Risk That Actually Matters Now

Every risk this project faced for the last fifteen documents was "we don't know X yet." That risk is gone. The risk that replaces it is the opposite one, and it is just as capable of stalling a solo project:

**Building all seven Capabilities, ten Repository packages, and every DOC-012 schema in parallel, polished, before any single fact has ever actually flowed from a real chain into a real Postgres row.**

Nothing below is sequenced to be clever. It is sequenced to get one true vertical slice — one real block, one real fact, one real row — working end to end as fast as possible, and only then to widen it. Every capability this project will ever need is already fully designed. None of them needs to be fully *built* before the others are started.

---

# Day 0 — Environment, Before Any Capability Code

Nothing here is a design decision — everything below was already decided in DOC-010/DOC-011. This is just the order to type it in.

- [ ] `uv init`, then add the DOC-010 dependency set: `pydantic`, `sqlalchemy[asyncio]`, `alembic`, `web3` (or the chosen provider client), `redis`, `polars`, `fastapi`, `streamlit`, `structlog`, `httpx`, plus dev-only `pytest`, `pytest-asyncio`, `hypothesis`, `ruff`, `mypy`, `import-linter`.
- [ ] Scaffold the exact tree from DOC-011 — empty packages and `__init__.py` files, no logic yet. An empty file that exists in the right place is worth more right now than a full one in the wrong place.
- [ ] `docker-compose.yml`: `postgres` (with the TimescaleDB extension enabled), `redis`. Not `platform` yet — there's nothing to containerize until Milestone 1 runs locally first.
- [ ] `alembic init`, and one empty baseline migration. Real migrations start at Milestone 1.
- [ ] Add the DOC-011 `import-linter` contracts to `pyproject.toml` (or `.importlinter`) and confirm `lint-imports` passes against the empty tree. This is cheaper to verify now, against nothing, than to debug later against something.
- [ ] `.env.example` with placeholder keys for one RPC provider (see § Open Decisions — any provider with a free tier for Base is fine to start; the `BlockchainProvider` interface exists specifically so this choice costs nothing to change later).
- [ ] `git init`, commit the empty, linted, contract-passing skeleton. This is commit #1 — it should already pass CI.

---

# Milestone 1 — The Walking Skeleton

**Goal:** one real `PairCreated` event, on one real chain, becomes one real row in `blockchain_facts` — and a second run of the same code against the same blocks produces the identical row.

**Why `PairCreated` first, and why Base:** `PairCreatedPayload` (DOC-012 § B.1) is the one payload with no Token Amount field in it — no `Decimal` handling, no Financial Precision questions, nothing to get wrong on the first attempt. It proves the pipeline shape with the least possible surface area. Base is the cheapest, fastest-confirming of the three EVM-first chains (DOC-006: ~2s blocks) — the fastest feedback loop for iterating on the collector itself.

**What gets built:**
- `acquisition/providers/base.py` — the `BlockchainProvider` interface (ADR-006), and one concrete implementation for whichever provider was picked in Day 0.
- `acquisition/collector.py` — polls or subscribes for `PairCreated` logs on one factory contract. No Redis Streams yet: a direct function call from Collector to Fact Processor is the correct amount of architecture for one fact type on one chain (DOC-004's own principle — simple over sophisticated, optimize after a real bottleneck appears, not before).
- `processing/normalizer.py` + `processing/fact_processor.py` — raw log → `BlockchainFact` with `confirmation_status = PENDING`. Full Confirmation Lifecycle (Confirmed/Finalized/Orphaned) is **not** in scope yet — that's Milestone 2, deliberately separated so this milestone stays small enough to actually finish.
- `persistence/postgres/facts.py` — the `blockchain_facts` table (DOC-014), insert path only.
- `domain/schemas/blockchain_fact.py`, `domain/schemas/enums.py` — typed directly from DOC-012 § B.1. This file should require zero new decisions; if it does, that's a sign DOC-012 has a gap worth returning to fix there, not patching locally.

**Definition of done:**
- [x] A real `PairCreated` event on Base produces a real row, correct in every field.
- [x] Running the same block range twice produces the same row, not a duplicate — idempotency (ADR-006) via the natural key, proven, not assumed.
- [x] The first Replay Test exists: `tests/replay/fixtures/` has one small, fixed, real block range; `tests/replay/test_replay.py` runs it through the live pipeline and asserts the output. It only needs to cover `PairCreated` right now — it grows with every later milestone, it does not need to be complete today.
- [x] `lint-imports` still passes.

---

# Milestone 2 — Finality: Confirmation Lifecycle and Reorg Handling

**Goal:** a `BlockchainFact` correctly moves `PENDING → CONFIRMED → FINALIZED`, and a real (or simulated) reorg correctly produces `ORPHANED` instead of a silently wrong row.

**What gets built:**
- `processing/finality_engine.py` — the header-buffer reorg detection and per-chain confirmation depth (ADR-006 § Finality Engine). This is the single highest correctness bar in the repository (DOC-011 already says so) — it earns its own milestone rather than being folded into Milestone 1.
- `persistence/postgres/facts.py` gains the row-level immutability guard (DOC-013 § Immutability & State Modeling): no `UPDATE` may touch a row once `confirmation_status = FINALIZED`.
- `persistence/postgres/facts.py` also gains the `checkpoints` table (DOC-014 § B.0) and `acquisition/checkpoint.py` (read path) / `processing/finality_engine.py` (write path, on finalization only — DOC-011's split).
- `config/confirmation_depth.yaml` (DOC-011) gets its first real values — see § Open Decisions.

**Definition of done:**
- [x] A simulated reorg (replaying a block range where a later fetch shows a different `block_hash` at the same height) correctly produces `ORPHANED`, not a wrong `FINALIZED` row.
- [x] Killing the collector process mid-block and restarting resumes from the checkpoint without reprocessing already-finalized facts or losing pending ones (DOC-013 § Async Conventions — graceful shutdown).
- [x] Replay Test fixtures now include at least one known reorg case.

---

# Milestone 3 — `SwapExecuted`, Financial Precision, and Market Bars

**Goal:** real swaps become real `Decimal`-precise facts, and a real OHLCV bar is correct and reproducible.

**What gets built:**
- `SwapExecutedPayload` handling in `fact_processor.py` — the first real test of DOC-012's discriminated union and the Financial Precision Principle (DOC-008) in actual code: every amount is `Decimal` from the moment it's parsed, never `float`, never a native JSON number.
- `analytics/trade_aggregator.py` — Market Bar generation, built **only** from `FINALIZED` (or explicitly `CONFIRMED`-with-`is_provisional=true`) `SwapExecuted` facts, per DOC-012's reconstruction predicate. This is the piece that was rewritten twice during design (once to stop deriving Bars from Snapshots, once to fix the `source_fact_range` query example) — worth re-reading DOC-012 § B.3 and DOC-014 § Indexing Strategy immediately before writing this file, not from memory.
- `persistence/timescale/repositories.py` — first hypertable (`market_bars`), with the partitioning and compression policy from DOC-014.

**Definition of done:**
- [ ] A known historical swap sequence produces byte-identical OHLCV values on replay (DOC-010 § Testing — `Decimal` fields are zero-tolerance, not approximate).
- [ ] A bar whose underlying facts include one that later orphans is fully recomputed, never patched (DOC-012's explicit rule).

---

# Milestone 4 — Domain Management

**Goal:** `Token`, `TradingPair`, `LiquidityPool` exist as real, queryable entities, not just implied by Facts.

**What gets built:** `domain_management/entity_resolution.py`, `domain_management/wallet_service.py`, `domain_management/metadata_service.py`; `persistence/postgres/models.py` (Part A tables). Metadata enrichment can start as a stub (empty/`UNVERIFIED` for every token) — real provider integration is a Day-1-of-this-milestone decision, not a blocker to starting it (see § Open Decisions).

**Definition of done:** every `TradingPair` from Milestone 1–3's facts has a resolved `Token` on both sides, with a stable Canonical ID.

---

# Milestone 5 — State Projection and Observation Snapshots

**Goal:** "what does this pool look like right now" (Redis, `StateProjection`) and "what did it look like at 14:32 on Tuesday" (TimescaleDB, `ObservationSnapshot`) both answer correctly.

**What gets built:** `analytics/projection_engine.py`, `transport/state_cache.py`, second and third hypertables (`observation_snapshots`, plus indexes from DOC-014).

**Definition of done:** killing and restarting the Projection Engine rebuilds identical state purely by replaying Facts — the concrete proof of DOC-006's "State can always be reconstructed."

---

# Milestone 6 — Feature Engineering

**Goal:** the first two or three real Features, Polars-backed, Point-in-Time correct.

**What gets built:** `analytics/feature_engine.py`. Start with something simple and well-understood — `liquidity_growth_pct_1h` and `price_momentum_zscore_1h` are enough to prove the shape. Every name gets a suffix from DOC-012 § Feature Naming Convention before it's merged, not after.

**Definition of done:** a backtest-style query (`as_of` set to a past timestamp) and a live query (`as_of` defaulted to now) for the same feature use the *same code path* — this is the actual, executable meaning of "Point-in-Time correctness," not a principle to take on faith.

---

# Milestone 7 — Intelligence (Basic Risk Analysis)

**Goal:** a deterministic risk read on a pair — MVP's original core hypothesis (DOC-003), now finally reachable.

**What gets built:** `intelligence/risk_rules.py`. Build vs. buy was already decided in this project's very first review round: consume a commodity risk API (GoPlus or equivalent) as one input feature rather than re-deriving honeypot/ownership heuristics from raw bytecode. This is the milestone where that decision stops being a recommendation and becomes an actual API key and an actual HTTP client with an actual timeout (DOC-013 § Async Conventions).

**Definition of done:** a newly discovered pair gets a risk read within the same latency budget as its first Feature computation — this is a research tool, not a batch report.

---

# Milestone 8 — Outcome Engine

**Goal:** ground-truth labels (`RUG_PULL`, `SUCCESSFUL_LAUNCH`, `DEAD_TOKEN`) exist for pairs whose observation window has closed.

**What gets built:** `analytics/outcome_engine.py` (or wherever the team ultimately resolved the DOC-006 "Analytics Engine" vs. `intelligence/` ownership note — this was left as an explicitly open, non-blocking naming question in DOC-011's last revision; resolve it here, don't let it block the milestone).

**Definition of done:** the first cohort of pairs old enough to evaluate (per DOC-012's `observation_window`) gets a real, versioned `label_definition` applied — this is what Phase 4 (ML Foundation, DOC-005) will eventually train against, so getting the definition right now is worth the small delay.

---

# Milestone 9 — Research Platform: API and Dashboard

**Goal:** a human — or an agent — can actually ask the platform a question instead of querying Postgres by hand.

**What gets built:** `research/api/` (every DOC-015 endpoint, in the order listed there — `/health` and `/pairs` first, `/pairs/{id}/dataset` last, since it depends on everything above already working), `research/dashboard/` (Streamlit, thin, over the same API — never a second data path).

**Definition of done:** DOC-002's own success definition, tested for real — pick one of its example questions ("why did this token gain momentum") and answer it using only the API, not a database client.

---

# Milestone 10 — Strategy

**Goal:** candidates are ranked, not just observed.

**What gets built:** `strategy/ranking.py` — deterministic, rule-based, explicitly scoped to *research* prioritization (DOC-009's own boundary: this capability recommends, it does not act).

**Definition of done — and MVP done, per DOC-003's actual Exit Criteria:** you, the researcher, choose this platform over the old fragmented workflow (DOC-002) for investigating a newly launched pair, consistently, not as a novelty.

---

# What Not To Build Yet

Every item below has a home already reserved for it (DOC-005's later phases, DOC-009's Future Capabilities) — reserved specifically so building it now would be premature, not forgotten:

- Anything from DOC-003's Non-Goals list — trading execution, portfolio management, wallet auth, notifications, copy trading.
- `AI Platform` — no LangChain, no RAG, no agent orchestration. DOC-009 marks this explicitly deferred to Roadmap Phase 6; the Capability-to-Technology table in DOC-010 has no row for it on purpose.
- A second chain family (Solana or otherwise). All three EVM-first chains are in scope; nothing else is, until the pipeline above is validated once (DOC-003).
- Kubernetes, multi-region, or any deployment topology beyond `docker-compose` on one machine.
- A `BEFORE UPDATE` trigger enforcing Fact immutability at the database level (DOC-014 already notes the application-level guard is sufficient for MVP) — a good Phase 2 hardening task, not a Milestone 1–10 one.

If a milestone above starts growing a ninth Capability nobody asked for, that is the signal to stop and re-open DOC-009, not to keep building.

---

# Open Decisions — Make These *During* the Relevant Milestone, Not Before

None of these block starting. Each is cheap to decide at the moment it's actually needed, and expensive to over-deliberate now:

- **First RPC provider (Milestone 1).** Any provider with a free tier covering Base is fine — Alchemy is a reasonable default with broad EVM-first coverage. The `BlockchainProvider` interface (ADR-006) is the reason this costs nothing to revisit.
- **`confirmation_depth` per chain (Milestone 2).** Needs a real number per chain in `config/confirmation_depth.yaml` — start conservative (deeper than strictly necessary) for Ethereum and BNB Chain, shallower for Base given its faster, more deterministic finality characteristics; tighten only once real reorg frequency is observed, not from a guess.
- **Metadata provider (Milestone 4).** Whether token metadata comes from a paid API, a free one, or a stub returning `UNVERIFIED` for everything — doesn't block resolving entities, only enriching them.
- **`avg_block_time_seconds` seed values (Milestone 1's `Blockchain` rows).** Seed with each chain's current published average and treat as a config value to correct later, not a constant to get exactly right on day one.
- **The `90-day` dataset span cap (DOC-015, relevant at Milestone 9).** Stated there as a starting number, not a permanent one — revisit once a real `/dataset` query's actual latency is known.

---

# Continuous Practices, From Commit 1

Not a milestone — true for all of them:

- DOC-013's Definition of Done checklist applies to every PR starting with Milestone 1's first commit, not once the codebase is "big enough to need it."
- `lint-imports` (DOC-011) runs in CI from Day 0's empty skeleton onward — it should never be the thing that first fails on a large, hard-to-untangle PR.
- Every milestone that touches `BlockchainFact` extends the Replay Test fixtures — the suite grows with the pipeline, it is never a separate task scheduled for "later."
- A new `Feature` is not merged without a naming-convention suffix (DOC-012); a new exception crossing a Capability boundary is not merged unless it's a `PlatformError` subclass (DOC-013). These are checklist items, not judgment calls, by design.

---

# Document Map — Where To Look When Stuck

| Question | Document |
|---|---|
| "What is this concept, conceptually?" | DOC-006 Domain Model, DOC-008 Canonical Glossary |
| "What does this field look like — exact type, exact JSON?" | DOC-012 Canonical Schema Specification |
| "Which package can import which?" | DOC-011 Repository Structure |
| "Which database, which column type, which index?" | DOC-014 Persistence Policy |
| "What does the API return, and why is it shaped that way?" | DOC-015 API Contracts |
| "How do I actually write this — exceptions, determinism, logging?" | DOC-013 Coding Standards |
| "Why RPC-and-build instead of a managed indexer?" | ADR-006 |
| "Why this library, and what replaces it later?" | DOC-010 Technology Stack |
| "Is this in scope for the MVP at all?" | DOC-003 MVP, § Non-Goals |
| "What comes after the MVP?" | DOC-005 Roadmap |

---

# Guiding Principle

> Fifteen documents exist so that the code, once written, has nothing left to guess. That only pays off if the code gets written — starting now, in the order above, one milestone at a time, not one more document at a time.