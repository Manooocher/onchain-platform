# Milestone 2 Execution Plan — Finality & Reorg Handling

Status: Planning artifact. Implements `docs/implementation/ImplementationPlan.md` § Milestone 2.
Prepared: 2026-08-19, after re-reading ImplementationPlan § Milestone 2, ADR-006 § Finality &
Canonical Chain Validation Engine / § Checkpointing / § Failure Recovery, DOC-012 § B.0 / § B.5,
DOC-013 § Immutability & State Modeling, DOC-014 § B.0 / § Migration Policy.

Goal (verbatim from ImplementationPlan): a `BlockchainFact` correctly moves `PENDING → CONFIRMED →
FINALIZED`, and a real (or simulated) reorg correctly produces `ORPHANED` instead of a silently
wrong row.

---

## 0. Pre-Flight Status

Verified against HEAD 7dda170 (commands run, not assumed):

| Item | Status | Detail |
|---|---|---|
| M1 gates pass on committed tree | ✅ Done | ruff clean, mypy strict 49 files 0 issues, lint-imports 8/8, 25 unit+integration+schema tests, 3 replay tests, live smoke — all verified fresh on HEAD |
| `config/` directory does NOT exist | ✅ Confirmed | `ls config/` → No such file or directory |
| `config/confirmation_depth.yaml` does NOT exist | ✅ Confirmed | Part of config/ absence |
| `blockchain_facts` has NO row-level immutability guard | ✅ Confirmed | `SELECT conname FROM pg_constraint WHERE conrelid='blockchain_facts'::regclass AND contype='t'` → 0 rows |
| `checkpoints` table EXISTS (M1 migration) | ⚠️ Already exists | Created in migration c3e9d2f5a817 with correct DOC-014 schema (chain_id PK, last_finalized_block, last_finalized_at, updated_at). No rows yet. No migration needed — just start using it. |
| `acquisition/checkpoint.py` does NOT exist | ✅ Confirmed | `ls` → No such file |
| `processing/finality_engine.py` does NOT exist | ✅ Confirmed | `ls` → No such file |
| Replay Test fixtures contain ZERO reorg cases | ✅ Confirmed | Only `base_pair_created_13500000_13500024.json` (canonical chain, no divergence) |
| `local_node.py` supports `parentHash` from `eth_getBlockByNumber` | ⚠️ Needs change | RPC returns `parentHash` (verified live: block 13,500,004 → `0x6b628a47...`), but `BlockMetadata` in `base.py` currently models only `number`, `hash`, `timestamp` — no `parent_hash` field. Must be added (step 1). |

**Pre-flight summary:** one structural change needed (`BlockMetadata.parent_hash`), one table
already exists (checkpoints — no migration needed), everything else is absent as expected.

---

## 1. Open Decisions — Resolved

| Decision | Resolution | Rationale |
|---|---|---|
| `confirmation_depth` per chain | **Base: 3, Ethereum: 12, BNB Chain: 8** | ImplementationPlan § Open Decisions: "start conservative… shallower for Base given its faster, more deterministic finality characteristics." ADR-006 example shows `base: 3`. Base has ~2s blocks and OP Stack deterministic finality — 3 blocks (6s) is conservative for a chain with near-zero historical reorgs. Ethereum: 12 blocks (~144s) is the canonical finality threshold. BNB Chain: 8 blocks (~6s, BNB has fast blocks but occasional instability). All stored in `config/confirmation_depth.yaml`, never hardcoded (ADR-006 § Configurable Confirmation Depth). |
| Header buffer storage | **In-memory only** | ADR-006 § Canonical Chain Validation Engine explicitly says "in-memory buffer of the last N block headers." On crash, recovery re-fetches headers from the checkpoint forward (ADR-006 § Recovery Procedure: "Load checkpoint → Replay missing blocks"). Persisting the buffer adds complexity with no benefit — the blockchain is always the source of truth (ADR-006: "Redis accelerates the pipeline. Blockchain preserves the truth."). |
| Reorg simulation strategy | **(a) Mock provider with two "views"** — a `ReorgSimulatorProvider` that serves a canonical chain for the first N blocks, then on re-fetch serves a different `block_hash` at the fork point. This is deterministic, self-contained, and doesn't depend on finding a real historical reorg. | Option (b) is non-deterministic (real reorgs are rare and hard to locate). Option (c) (fixture file with two views) is equivalent to (a) but less flexible. The mock provider approach lets us test single-block and multi-block reorgs with exact control over the fork point. |
| Checkpoint advancement granularity | **Per-finalization-batch** — advance the checkpoint to the highest block whose facts all reached `FINALIZED` in this pass. ADR-006 § Checkpoint Strategy: "the highest finalized block that has been completely processed." Per-block writes are wasteful; per-batch matches the natural finality window. | The finality engine processes blocks in order; when block N is confirmed at depth D, all facts in block N-D become FINALIZED. The checkpoint advances to N-D. This is the minimum safe checkpoint — never past a block that hasn't been fully finalized. |
| `ORPHANED` fact retention | **ORPHANED rows remain in `blockchain_facts`, queryable for audit.** ADR-006 § Orphaned: "The Fact remains stored for auditability but is excluded from downstream processing. Facts are never deleted. Only their confirmation status changes." No DELETE, no soft-delete flag beyond `confirmation_status`. | This is unambiguous in ADR-006. The row-level immutability guard applies to FINALIZED rows only — ORPHANED rows are write-once (insert) then status-change-once (→ ORPHANED), after which they too become immutable. |
| `ChainReorgEvent` publishing | **Schema created in M2, publishing deferred to the milestone that adds `transport/event_stream.py`.** DOC-013 § Exception Hierarchy: "Reorgs are modeled as Domain Events published to Redis Streams." But M2 has no Redis Streams integration (out of scope). The finality engine will produce `ChainReorgEvent` objects in-memory and log them; actual Redis publishing is wired when `transport/event_stream.py` arrives. | Creating the schema now (DOC-012 § B.5) ensures the type exists for the finality engine to construct. Deferring the transport avoids introducing Redis Streams prematurely. |

---

## 2. Build Order (Sequential)

Gates: every step passes `make lint && make typecheck && make import-check` before the next begins.

### Phase A: Domain & Config (no DB changes, no Capability code)

1. **`src/onchain_platform/acquisition/providers/base.py`** — add `parent_hash: str` to `BlockMetadata`.
   - Purpose: the Finality Engine verifies chain continuity by checking that each block's
     `parent_hash` matches the previous block's `hash` across the confirmation window (ADR-006 §
     Canonical Chain Validation Engine). Without `parent_hash` on `BlockMetadata`, the engine
     cannot do its job.
   - Deps: none.
   - Verification: `mypy src/` clean; existing tests still pass (the field is additive, frozen
     model, no existing code reads it yet).
   - Complexity: trivial.

2. **`src/onchain_platform/acquisition/providers/local_node.py`** — extract `parentHash` from
   `eth_getBlockByNumber` response into `BlockMetadata.parent_hash`.
   - Deps: step 1.
   - Verification: unit test asserting `get_block_metadata` returns the correct `parent_hash` for
     a known block (block 13,500,004 → `0x6b628a4744f41af5c3ba80d4bc898421c074a751dc9f91a71325812d11d36dcd`,
     verified live during planning).
   - Complexity: trivial.

3. **`config/confirmation_depth.yaml`** — per-chain confirmation depth values.
   - Purpose: ADR-006 § Configurable Confirmation Depth: "The platform must not hardcode
     confirmation rules." Values: `{ethereum: 12, base: 3, bnb: 8}` (§1 above).
   - Deps: none.
   - Verification: file exists, YAML parses, values match §1.
   - Complexity: trivial.

4. **`src/onchain_platform/domain/schemas/checkpoint.py`** — `Checkpoint` schema (DOC-012 § B.0).
   - Fields: `chain_id: int`, `last_finalized_block: int`, `last_finalized_at: datetime`,
     `updated_at: datetime`. Frozen model per DOC-013.
   - Note: the `checkpoints` table already exists (M1 migration). This schema is the domain-side
     counterpart — the repositories translation boundary needs it.
   - Deps: none.
   - Verification: unit test round-trip, frozen-mutation rejection.
   - Complexity: trivial.

5. **`src/onchain_platform/domain/schemas/chain_reorg_event.py`** — `ChainReorgEvent` schema
   (DOC-012 § B.5).
   - Fields per DOC-012: `schema_version`, `event_id` (`f"{chain_id}|{fork_block_number}|{detected_at.isoformat()}"`),
     `chain_id`, `fork_block_number`, `orphaned_block_range: tuple[int, int]`,
     `new_canonical_head_hash`, `depth`, `detected_at`. Frozen model.
   - Deps: none.
   - Verification: unit test round-trip, event_id format validation, frozen-mutation rejection.
   - Complexity: trivial.

6. **`tests/schema/test_canonical_schemas.py`** — extend with hypothesis tests for `Checkpoint`
   and `ChainReorgEvent`.
   - Deps: steps 4, 5.
   - Verification: `make test` green.
   - Complexity: trivial.

### Phase B: Persistence — Immutability Guard & Lifecycle Transitions

7. **`src/onchain_platform/persistence/postgres/facts.py`** — add `update_fact_confirmation`
   function with row-level immutability guard.
   - Purpose: DOC-013 § Immutability & State Modeling: "Once a row lands with
     `confirmation_status = FINALIZED`, no `UPDATE` may ever touch it again. This is enforced in
     exactly one place: `persistence/postgres/facts.py`, as a guard evaluated before any `UPDATE`
     statement."
   - Logic: before executing UPDATE, SELECT `confirmation_status` for the target `fact_id`. If
     `FINALIZED`, raise `PersistenceError` — never a silent no-op (DOC-013: "A violation raises
     PersistenceError, not a silent no-op").
   - Transition rules enforced here:
     - `PENDING → CONFIRMED` (increment confirmations, update status)
     - `CONFIRMED → FINALIZED` (increment confirmations, update status)
     - `any → ORPHANED` (update status, set confirmations to 0)
     - `FINALIZED → anything` → `PersistenceError` (the one forbidden transition)
   - Also: `update_confirmation_counts(chain_id, new_confirmations)` — bulk update for advancing
     confirmations on all non-finalized, non-orphaned facts for a chain.
   - Deps: existing `facts.py` (M1).
   - Verification: unit tests asserting every legal transition succeeds, FINALIZED→anything
     raises PersistenceError, ORPHANED→ORPHANED is idempotent.
   - Complexity: high (highest correctness bar — every transition rule must be right).

8. **`src/onchain_platform/persistence/postgres/repositories.py`** — add checkpoint read/write
   translation boundary.
   - `get_checkpoint(session, chain_id) -> Checkpoint | None` — read path.
   - `save_checkpoint(session, checkpoint: Checkpoint) -> None` — upsert (INSERT ON CONFLICT
     UPDATE). Checkpoint is mutable (DOC-012 § B.0), so this is a normal upsert, not the
     immutability-guarded path.
   - Deps: step 4.
   - Verification: integration test — save checkpoint, read back, assert fields; upsert overwrites.
   - Complexity: moderate.

### Phase C: Finality Engine — the core artifact

9. **`src/onchain_platform/processing/finality_engine.py`** — the single highest correctness bar
   in the repository (DOC-011).
   - **Header buffer:** in-memory `deque[BlockMetadata]` of the last N headers (N = confirmation
     depth for the chain, loaded from `config/confirmation_depth.yaml`). On startup, the buffer
     is empty — it fills as new blocks arrive. Finalization cannot begin until the buffer has N
     entries (the first N-1 blocks are PENDING/CONFIRMED only).
   - **Continuity verification (ADR-006 § Canonical Chain Validation Engine):** on every new block,
     verify that `buffer[i].parent_hash == buffer[i-1].hash` for all i in [1, N). If any link
     breaks → reorg detected. This is the confirmation WINDOW check, not a single-block comparison
     (ADR-006: "Checking only the immediate parent hash is insufficient for EVM networks").
   - **Confirmation advancement:** when a new block arrives and continuity holds, increment
     `confirmations` for all PENDING/CONFIRMED facts on this chain. Any fact whose
     `confirmations >= confirmation_depth` transitions to FINALIZED.
   - **Checkpoint advancement:** after finalization, advance the checkpoint to the highest
     FINALIZED block number (per-finalization-batch granularity, §1).
   - **Reorg handling:** on continuity break, determine the fork point (first block in the buffer
     whose `parent_hash` doesn't match the previous block's `hash`). All facts with
     `block_number >= fork_block_number` and `confirmation_status IN (PENDING, CONFIRMED)` are
     marked ORPHANED. Facts already FINALIZED before the fork point are untouched (they were
     beyond the reorg depth). Construct a `ChainReorgEvent` and log it (publishing to Redis
     Streams deferred).
   - **Determinism:** no wall-clock reads (DOC-013). The engine receives block metadata as
     parameters. `detected_at` on `ChainReorgEvent` comes from the injected clock.
   - **Single Processing Path (ADR-006):** the same engine processes both live and replayed
     blocks — no separate "historical mode."
   - Deps: steps 1–5, 7, 8.
   - Verification: unit tests with synthetic block sequences:
     - Happy path: N blocks arrive with valid continuity → facts transition PENDING→CONFIRMED→FINALIZED
     - Single-block reorg: fork at block N-1 → affected facts ORPHANED, pre-fork FINALIZED untouched
     - Multi-block reorg: fork at block N-3 → same
     - Buffer not yet full: first N-1 blocks → no finalization, only confirmation advancement
     - Idempotent: same block sequence twice → same final state
   - Complexity: **high** (the milestone's raison d'être).

### Phase D: Integration — wiring finality into the pipeline

10. **`src/onchain_platform/acquisition/collector.py`** — integrate finality engine.
    - After the fact processor emits a PENDING fact, the collector calls
      `finality_engine.on_new_block(block_metadata)` which handles confirmation advancement,
      finalization, and reorg detection.
    - The collector already processes blocks in order (M1). The finality engine is called once
      per block, after all facts for that block are persisted as PENDING.
    - Graceful shutdown (already exists in M1): finish in-flight block before exit. The finality
      engine's state is in-memory only — on restart, the checkpoint tells us where to resume, and
      the header buffer rebuilds from the re-fetched blocks.
    - Deps: step 9.
    - Verification: unit test with FakeProvider + FinalityEngine asserting the full pipeline
      (collector → processor → finality → persistence) produces FINALIZED facts after depth
      blocks.
    - Complexity: moderate.

11. **`src/onchain_platform/main.py`** — wire finality engine into the composition root.
    - Load `config/confirmation_depth.yaml` at startup.
    - Construct `FinalityEngine(chain_id, confirmation_depth, session_factory, clock)`.
    - Pass the engine to the collector.
    - On startup: load checkpoint, resume from `last_finalized_block + 1` (ADR-006 § Recovery
      Procedure). If no checkpoint exists (first run), start from the configured `--start-block`
      or chain head.
    - Deps: steps 3, 9, 10.
    - Verification: `uv run onchain-platform --start-block 13500004` processes one block, fact
      persists as PENDING (depth not yet reached), checkpoint not advanced (block not finalized).
    - Complexity: moderate.

### Phase E: Tests — reorg fixture, lifecycle, checkpoint recovery

12. **`tests/replay/fixture_provider.py`** — extend with `ReorgSimulatorProvider`.
    - A `BlockchainProvider` that serves a canonical chain for blocks [A..B], then on re-fetch
      of block B serves a different `block_hash` (and different `parent_hash` chain). This
      simulates a reorg: the finality engine's header buffer detects the continuity break.
    - The provider is deterministic — the "divergent" block hash is a committed constant, not
      random.
    - Deps: step 1.
    - Verification: unit test asserting the provider returns different hashes for the same block
      number on first vs second call.
    - Complexity: moderate.

13. **`tests/replay/test_replay.py`** — extend with reorg replay test.
    - New fixture: `tests/replay/fixtures/base_reorg_simulation.json` — a synthetic fixture
      covering blocks [13,500,000..13,500,010] where block 13,500,008 has a divergent hash on
      re-fetch. Facts from blocks 13,500,008–13,500,010 should become ORPHANED; facts from
      13,500,000–13,500,007 should reach FINALIZED (if confirmation depth ≤ 3).
    - Assert: ORPHANED facts have `confirmations = 0`, FINALIZED facts have
      `confirmations >= confirmation_depth`.
    - Assert: second run of the same fixture produces byte-identical output (ADR-006 Principle 2).
    - Deps: steps 9, 12.
    - Verification: `make test-replay` green.
    - Complexity: high.

14. **`tests/unit/test_finality_engine.py`** — comprehensive unit tests for the finality engine.
    - `test_pending_to_confirmed_to_finalized_lifecycle` — synthetic block sequence, verify
      status transitions at each step.
    - `test_single_block_reorg_produces_orphaned` — fork at block N, verify ORPHANED for
      affected facts, FINALIZED for pre-fork facts.
    - `test_multi_block_reorg_produces_orphaned` — fork at block N-2, same verification.
    - `test_immutability_guard_rejects_update_on_finalized` — attempt to update a FINALIZED
      fact's confirmation_status, assert PersistenceError.
    - `test_checkpoint_advances_only_on_finalization` — verify checkpoint doesn't advance for
      PENDING/CONFIRMED blocks.
    - `test_header_buffer_not_full_no_finalization` — first N-1 blocks, no FINALIZED facts.
    - `test_replay_idempotent` — same block sequence twice, same final state.
    - Deps: steps 7, 9.
    - Verification: `make test` green.
    - Complexity: high.

15. **`tests/integration/test_finality_lifecycle.py`** — integration tests against real Postgres.
    - `test_pending_confirmed_finalized_lifecycle_real_db` — insert PENDING facts, run finality
      engine, verify FINALIZED in DB.
    - `test_reorg_marks_orphaned_not_finalized` — insert facts, simulate reorg via
      ReorgSimulatorProvider, verify ORPHANED in DB.
    - `test_checkpoint_recovery_after_restart` — insert facts, advance checkpoint, "restart"
      (new engine instance), verify it resumes from checkpoint without reprocessing finalized facts.
    - Deps: steps 7, 8, 9, 12.
    - Verification: `make test` green with compose up.
    - Complexity: high.

16. **`tests/integration/test_checkpoint_recovery.py`** — the DoD "kill mid-block, restart" test.
    - Start collector with finality engine, process blocks [A..C], kill after block B's facts
      are persisted but before block C's finality pass completes.
    - Restart from checkpoint. Assert: block A's facts are FINALIZED, block B's facts are
      PENDING (not lost, not duplicated), block C is reprocessed.
    - Deps: steps 10, 11.
    - Verification: `make test` green.
    - Complexity: high.

### Phase F: Final gate

17. **Final gate + commit.**
    - `make lint && make typecheck && make import-check && make test && make test-replay` all green.
    - Update ImplementationPlan.md Milestone 2 DoD checkboxes.
    - Commit.

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Header buffer lost on process crash → recovery fails to reconstruct | Low | High | Buffer is in-memory by design (ADR-006). Recovery re-fetches headers from checkpoint forward. The checkpoint is the durable state; the buffer is rebuilt. Test: checkpoint_recovery test (step 16). |
| ORPHANED applied to wrong block range (off-by-one in fork detection) | Medium | High | Fork point = first block in buffer whose `parent_hash` ≠ previous block's `hash`. Unit tests with exact block numbers (step 14). The boundary condition is `block_number >= fork_block_number` — test both `==` and `>` cases. |
| Checkpoint advanced past a block that wasn't actually finalized | Low | High | Checkpoint advances only after finalization pass completes. The guard: `checkpoint.last_finalized_block = max finalized block number in this pass`. Never advance past the buffer's oldest block minus confirmation depth. |
| FINALIZED-row immutability guard bypassed by raw SQL or migration | Low | Medium | Application-level guard is primary (DOC-014: "sufficient for MVP"). A `BEFORE UPDATE` trigger is explicitly deferred to Phase 2 hardening (ImplementationPlan § What Not To Build Yet). Document this in the guard's docstring. |
| Collector crashes mid-event, checkpoint points past partially-processed block | Medium | Medium | DOC-013 § Async Conventions: "finishes processing whatever event is currently in flight." The checkpoint advances only after the full block's facts are persisted AND the finality pass completes. If crash happens between fact persistence and finality pass, on restart the block is re-fetched, facts are idempotent (ON CONFLICT DO NOTHING), and finality runs again. |
| Confirmation depth too shallow for Base → legitimate reorgs slip through as FINALIZED | Low | Medium | Base has near-zero historical reorgs (OP Stack deterministic finality). Depth 3 (6s) is conservative. Config is YAML — tighten without code changes if reorgs are observed. |
| ChainReorgEvent publishing added prematurely before transport/event_stream.py | Low | Low | Schema created, objects constructed in-memory, logged via structlog. No Redis dependency. Publishing wired when transport/ lands. |
| Replay Test reorg fixture becomes flaky due to non-deterministic mock behavior | Low | Medium | ReorgSimulatorProvider serves committed constants (hashes, timestamps). The "divergent" block hash is a fixed string, not generated. Deterministic by construction. |
| `eth_getBlockByNumber` returns `parentHash` inconsistently across providers | Low | Medium | Verified live against Base public RPC. The `parent_hash` field is lowercase-hex-normalized like all other hashes (local_node.py pattern). If a provider omits it, `AcquisitionError` is raised at the boundary. |
| Bulk UPDATE for confirmation advancement is slow on large fact tables | Low | Low | M1 has 5 facts. The index `(chain_id, confirmation_status)` (DOC-014) makes the UPDATE efficient. Monitor at scale; not a concern for MVP. |

---

## 4. Definition of Done Matrix

| DoD Item (ImplementationPlan § Milestone 2) | Verification Method | Automated? |
|---|---|---|
| Simulated reorg correctly produces ORPHANED, not wrong FINALIZED | `test_replay.py` reorg fixture + `test_finality_engine.py::test_single_block_reorg_produces_orphaned` + `test_finality_lifecycle.py::test_reorg_marks_orphaned_not_finalized` | Yes |
| Kill mid-block, restart resumes from checkpoint without reprocessing or losing pending facts | `test_checkpoint_recovery.py` — start, process, kill, restart, assert state | Yes |
| Replay Test fixtures include at least one known reorg case | `tests/replay/fixtures/base_reorg_simulation.json` committed + `test_replay.py` reorg test | Yes |
| PENDING → CONFIRMED → FINALIZED lifecycle works | `test_finality_engine.py::test_pending_to_confirmed_to_finalized_lifecycle` + `test_finality_lifecycle.py` | Yes |
| Row-level immutability guard rejects UPDATE on FINALIZED | `test_finality_engine.py::test_immutability_guard_rejects_update_on_finalized` | Yes |
| ChainReorgEvent schema exists | `test_canonical_schemas.py` hypothesis test for ChainReorgEvent | Yes |
| lint-imports still passes | `make import-check` | Yes |
| No new forbidden imports introduced | `make import-check` — processing/ may import acquisition/ (layers-legal), but not domain_management/, analytics/, etc. | Yes |
| No datetime.now() in Capabilities | grep gate + design: clock injected everywhere | Partially (grep + review) |
| Checkpoint table used correctly | `test_finality_engine.py::test_checkpoint_advances_only_on_finalization` + integration test | Yes |

---

## 5. Out-of-Scope Confirmation

Per ImplementationPlan § Milestone 2 and § What Not To Build Yet:

- [x] Redis Streams — NOT introduced. Direct function call pattern from M1 continues. `transport/` stays empty.
- [x] Market Bars, Features, State Projection, Snapshots — NOT built. `analytics/` stays empty.
- [x] SwapExecuted or any non-PairCreated fact type — NOT processed. Only PAIR_CREATED facts flow through the pipeline.
- [x] Domain Management / Entity Resolution — NOT built. `domain_management/` stays empty.
- [x] API endpoints, dashboard — NOT built. `research/` stays empty.
- [x] AI/RAG/agent code — out of MVP scope entirely.
- [x] Second chain beyond Base — Base remains the development chain. Ethereum/BNB seeds in `confirmation_depth.yaml` but not actively ingested.
- [x] `BEFORE UPDATE` database trigger for FINALIZED-immutability — application-level guard in `facts.py` is sufficient per DOC-014. Explicitly deferred to Phase 2 hardening.
- [x] No `utils/` or `common/` package introduced.
- [x] No UUID fact IDs — natural composite keys continue.
- [x] No offset-based pagination introduced.

---

## 6. Questions / Blockers

Q1 (needs human): The `checkpoints` table was created in M1's migration with `nextval('checkpoints_chain_id_seq')` as the default for `chain_id`. This is wrong — `chain_id` should be a plain BIGINT PK without a sequence (the chain ID is a known value, not auto-generated). The M1 migration's `sa.Column("chain_id", sa.BigInteger, primary_key=True)` generated a sequence because SQLAlchemy's default for BigInteger PKs is auto-increment. Options: (a) fix via a new migration that drops the sequence and default; (b) ignore — the upsert in repositories.py always specifies `chain_id` explicitly, so the default is never used. Recommendation: (b) for M2, fix in a cleanup pass — the sequence is harmless but untidy. Needs confirmation.

Q2 (design note, flagged per Document Resolution Protocol): ADR-006 § Canonical Chain Validation Engine says the engine "maintains an in-memory buffer of the last N block headers." The plan implements this as a `deque[BlockMetadata]` that fills incrementally. On restart, the buffer is empty and must be re-filled from the checkpoint forward before finalization can resume. This means the first N blocks after restart are PENDING/CONFIRMED only — no FINALIZATION until the buffer is full. This is correct behavior (the engine needs N headers to verify the window), but it means a restart has a brief "warm-up" period. Flagging so it's documented, not surprising.

Q3 (doc gap, not a blocker): DOC-012 § B.5 `ChainReorgEvent.orphaned_block_range` is typed as `tuple[int, int]`. DOC-014 doesn't specify a column type for this field because ChainReorgEvent is a Redis Streams event (B.5), not a persisted table row. Since M2 constructs these objects in-memory only (no Redis), the tuple type is fine. When transport/ lands, the Redis serialization format will need to be specified. Not a blocker for M2.

Q4 (needs human): The M1 `checkpoints` table has `last_finalized_at` and `updated_at` as separate columns. DOC-012 § B.0 defines both. The plan uses `last_finalized_at` to record when the block was finalized (block timestamp of the finalized block) and `updated_at` for when the row was last written (wall clock). The wall clock read for `updated_at` happens in the repository (persistence layer, not a Capability) — this is consistent with DOC-013's carve-out for platform/infrastructure code. Confirm this interpretation.

No hard blockers: every Q above has a stated fallback that keeps M2 buildable today.
