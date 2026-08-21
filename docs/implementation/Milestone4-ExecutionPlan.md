# Milestone 4 Execution Plan — Domain Management

Status: Planning artifact. Implements `docs/implementation/ImplementationPlan.md` § Milestone 4.
Prepared: 2026-08-20, after re-reading ImplementationPlan § Milestone 4, DOC-012 Part A (Entity
Schemas), DOC-014 § Storage Assignment / § Indexing Strategy / § Data Integrity Constraints,
DOC-006 § Structural Domain / § Ownership, DOC-011 § domain_management/ / § domain/.

Goal (verbatim from ImplementationPlan): `Token`, `TradingPair`, `LiquidityPool` exist as real,
queryable entities, not just implied by Facts.

---

## 0. Pre-Flight Status

Verified against HEAD b9bfc00 (commands run, not assumed):

| Item | Status | Detail |
|---|---|---|
| M3 gates pass | ✅ Done | ruff clean, mypy strict 65 files 0 issues, lint-imports 8/8, 60 fast + 5 replay + 1 live smoke |
| `persistence/postgres/models.py` does NOT exist | ✅ Confirmed | Only `facts.py` and `repositories.py` in `persistence/postgres/`. Part A ORM models not yet created. |
| `domain_management/entity_resolution.py` does NOT exist | ✅ Confirmed | `domain_management/` contains only `__init__.py`. |
| `domain_management/wallet_service.py` does NOT exist | ✅ Confirmed | Same. |
| `domain_management/metadata_service.py` does NOT exist | ✅ Confirmed | Same. |
| `domain/entities/token.py`, `trading_pair.py`, `liquidity_pool.py` do NOT exist | ✅ Confirmed | Only `blockchain.py` exists in `domain/entities/`. |
| `domain/enums.py` does NOT exist | ✅ Confirmed | Only `domain/exceptions.py` and `domain/__init__.py` at domain root. `domain/schemas/enums.py` has fact-lifecycle enums. |
| `domain/ids.py` does NOT exist | ✅ Confirmed | Canonical ID construction not yet implemented. |
| PairCreated payload has no FKs | ✅ Confirmed | `pair_address`, `token0_address`, `token1_address`, `dex` — all strings, no FK references. |
| SwapExecuted payload has no FKs | ✅ Confirmed | `pool_address`, `sender`, `recipient`, amounts — all strings. |
| `blockchain_facts` table has no FK to entity tables | ✅ Confirmed | `blockchain_facts` is self-contained (DOC-014 § B.1). |

**Pre-flight summary:** all M3 artifacts in place, all M4 prerequisites absent as expected. The
entity Pydantic schemas, ORM models, Canonical ID construction, and domain_management services
all need to be created from scratch.

---

## 1. Open Decisions — Resolved

| Decision | Resolution | Rationale |
|---|---|---|
| Entity Resolution trigger | **Eager, on PairCreated fact ingestion.** When a `PairCreated` fact is persisted, entity resolution runs synchronously in the same session: create `Token` entities for token0/token1 (if not already present), create `TradingPair` + `LiquidityPool` entities. For `SwapExecuted` facts: create `Wallet` entities for sender/recipient (if not already present). No lazy resolution. | ImplementationPlan § Milestone 4: "entity resolution should be synchronous within the fact processor, not a separate async pipeline (simplicity over sophistication, DOC-004)." Eager resolution ensures entities exist before any downstream query needs them. Idempotent upserts (ON CONFLICT DO NOTHING for inserts, ON CONFLICT DO UPDATE for first_seen_at) guarantee replay safety. |
| Metadata provider | **Stub returning `UNVERIFIED` for everything.** `metadata_service.py` creates `Metadata` rows with `verification_status=UNVERIFIED`, empty `website`/`social_links`/`logo_url`/`description`. Real provider integration deferred. | ImplementationPlan § Open Decisions: "Metadata enrichment can start as a stub (empty/UNVERIFIED for every token) — real provider integration is a Day-1-of-this-milestone decision, not a blocker to starting it." |
| Canonical ID stability | **Stable across reorgs.** Token: `eip155:<chain_id>/token:<address>`. TradingPair: `eip155:<chain_id>/pair:<pool_address>`. LiquidityPool: same as TradingPair. Wallet: `eip155:<chain_id>/wallet:<address>`. Addresses are immutable on-chain — reorgs change block history, not contract addresses. | DOC-012 Part A defines these formats. EVM addresses are deterministic from deployment tx — no reorg can change them. |
| Wallet Service scope | **Minimal: create Wallet entities when first seen in a fact.** `wallet_service.py` upserts a `Wallet` row with `first_seen_at = event_time` of the first fact referencing that address. No behavior analysis, no tagging, no Feature Engineering. | DOC-006 Ownership table: "Wallet Service" is distinct from "Entity Resolution." M4 scope: "Wallet entities when first seen in a fact." DOC-012 Part A Wallet schema: `tags` is "Empty in MVP. Placeholder for DOC-006 Future Extensions." |
| Foreign key strategy | **No FKs from `blockchain_facts` to entity tables.** `blockchain_facts.payload` fields (`pool_address`, `token0_address`, etc.) remain strings. Rationale: (1) `blockchain_facts` is append-only and immutable once FINALIZED — adding FKs would create a write-order dependency (entities must exist before facts). (2) DOC-014 § Data Integrity Constraints: "No foreign key where the reference is polymorphic" — while these specific references are monomorphic, the payload is JSONB and the addresses are inside the JSON, not top-level columns. (3) Entity resolution is eager but not guaranteed to complete before fact persistence in all edge cases (e.g., crash between fact insert and entity resolution). FKs from `trading_pairs.base_token_id` → `tokens.canonical_id` ARE added (monomorphic, top-level columns, DOC-014 § Data Integrity Constraints). | DOC-014 § Data Integrity Constraints: "Foreign keys where the reference is monomorphic" — `TradingPair.base_token_id` → `Token.canonical_id` is monomorphic and gets a FK. But `blockchain_facts.payload` is JSONB — FKs into JSONB are not supported by PostgreSQL. The addresses in the payload are queryable via `payload->>'pool_address'` but cannot be FK targets. |

---

## 2. Build Order (Sequential)

Gates: every step passes `make lint && make typecheck && make import-check` before the next begins.

### Phase A: Domain Layer (entity schemas + Canonical IDs)

1. **`src/onchain_platform/domain/ids.py`** — Canonical ID construction functions.
   - `token_canonical_id(chain_id: int, address: str) -> str` → `f"eip155:{chain_id}/token:{address}"`
   - `pair_canonical_id(chain_id: int, pool_address: str) -> str` → `f"eip155:{chain_id}/pair:{pool_address}"`
   - `wallet_canonical_id(chain_id: int, address: str) -> str` → `f"eip155:{chain_id}/wallet:{address}"`
   - `smart_contract_canonical_id(chain_id: int, address: str) -> str` → `f"eip155:{chain_id}/contract:{address}"`
   - All addresses EIP-55 checksummed (eth_utils, same pattern as blockchain_fact.py).
   - Deps: none.
   - Verification: unit tests asserting format matches DOC-012 Part A examples.
   - Complexity: trivial.

2. **`src/onchain_platform/domain/enums.py`** — Structural/registry enums (DOC-011 v1.5).
   - `ContractType(StrEnum)`: ERC20, FACTORY, ROUTER, POOL, UNKNOWN (DOC-012 Part A SmartContract).
   - `VerificationStatus(StrEnum)`: UNVERIFIED, PENDING, VERIFIED (DOC-012 Part A Metadata).
   - Deps: none.
   - Verification: unit tests asserting member values.
   - Complexity: trivial.

3. **`src/onchain_platform/domain/entities/token.py`** — Token entity schema (DOC-012 Part A).
   - Frozen Pydantic model. Fields: `schema_version`, `canonical_id`, `chain_id`, `contract_address` (EIP-55 checksummed), `symbol`, `name`, `decimals`, `total_supply` (str — Token Amount, DOC-008), `deployment_block`.
   - Validator: `canonical_id` must match `f"eip155:{chain_id}/token:{contract_address}"`.
   - Deps: step 1.
   - Verification: unit test round-trip, canonical_id consistency validator.
   - Complexity: trivial.

4. **`src/onchain_platform/domain/entities/trading_pair.py`** — TradingPair entity schema (DOC-012 Part A).
   - Frozen Pydantic model. Fields: `schema_version`, `canonical_id`, `chain_id`, `dex`, `base_token_id`, `quote_token_id`, `pool_address` (EIP-55), `creation_block`, `creation_fact_id`.
   - Validator: `canonical_id` must match `f"eip155:{chain_id}/pair:{pool_address}"`.
   - Deps: step 1.
   - Verification: unit test round-trip, canonical_id consistency validator.
   - Complexity: trivial.

5. **`src/onchain_platform/domain/entities/liquidity_pool.py`** — LiquidityPool entity schema (DOC-012 Part A).
   - Frozen Pydantic model. Fields: `schema_version`, `canonical_id` (same as TradingPair), `protocol`, `fee_tier_bps` (int | None).
   - DOC-012: "a Liquidity Pool does not have an identity independent of its pair in the MVP."
   - Deps: step 1.
   - Verification: unit test round-trip.
   - Complexity: trivial.

6. **`src/onchain_platform/domain/entities/wallet.py`** — Wallet entity schema (DOC-012 Part A).
   - Frozen Pydantic model. Fields: `schema_version`, `canonical_id`, `chain_id`, `address` (EIP-55), `first_seen_at` (datetime), `tags` (list[str], empty in MVP).
   - Deps: step 1.
   - Verification: unit test round-trip.
   - Complexity: trivial.

7. **`src/onchain_platform/domain/entities/smart_contract.py`** — SmartContract entity schema (DOC-012 Part A).
   - Frozen Pydantic model. Fields: `schema_version`, `canonical_id`, `chain_id`, `address` (EIP-55), `contract_type` (ContractType enum), `is_verified`, `deployment_block` (int | None).
   - Deps: steps 1, 2.
   - Verification: unit test round-trip.
   - Complexity: trivial.

8. **`src/onchain_platform/domain/entities/metadata.py`** — Metadata entity schema (DOC-012 Part A).
   - Frozen Pydantic model. Fields: `schema_version`, `entity_id`, `website` (str | None), `social_links` (dict[str, str]), `logo_url` (str | None), `description` (str | None), `verification_status` (VerificationStatus), `last_updated` (datetime).
   - DOC-006: "Metadata never modifies a Blockchain Fact. This schema has no event_time."
   - Deps: step 2.
   - Verification: unit test round-trip.
   - Complexity: trivial.

9. **`tests/unit/test_entity_schemas.py`** — Unit tests for all entity schemas + Canonical IDs.
   - Round-trip tests for each entity.
   - Canonical ID format tests (matches DOC-012 Part A examples).
   - Frozen mutation rejection tests.
   - Deps: steps 1–8.
   - Verification: `make test` green.
   - Complexity: trivial.

### Phase B: Persistence Layer (ORM models + migration)

10. **`src/onchain_platform/persistence/postgres/models.py`** — Part A ORM models.
    - `TokenRow`, `TradingPairRow`, `LiquidityPoolRow`, `WalletRow`, `SmartContractRow`, `MetadataRow`.
    - Column types per DOC-014 § Type Mapping Rules:
      - `canonical_id`: TEXT (DOC-014: "Never VARCHAR(n) with an arbitrary limit").
      - `contract_address`, `pool_address`, `address`: VARCHAR(42) (EIP-55 checksummed, always 42 chars).
      - `total_supply`: NUMERIC(78, 0) (Token Amount, uint256).
      - `decimals`: INTEGER (bounded by uint8).
      - `chain_id`, `deployment_block`, `creation_block`: BIGINT.
      - `fee_tier_bps`: INTEGER (bounded ≤ 10000, DOC-014 CHECK constraint).
      - `tags`: TEXT[] (native Postgres array).
      - `social_links`: JSONB.
      - `verification_status`: native Postgres ENUM.
      - `contract_type`: native Postgres ENUM.
      - All timestamps: TIMESTAMPTZ.
    - FK: `TradingPairRow.base_token_id` → `TokenRow.canonical_id`, `TradingPairRow.quote_token_id` → `TokenRow.canonical_id` (monomorphic, DOC-014 § Data Integrity Constraints).
    - FK: `LiquidityPoolRow.canonical_id` → `TradingPairRow.canonical_id` (DOC-014).
    - Deps: steps 1–8.
    - Verification: mypy clean, import-check passes.
    - Complexity: moderate.

11. **Alembic migration** — Create Part A tables + indexes + seed data.
    - Tables: `tokens`, `trading_pairs`, `liquidity_pools`, `wallets`, `smart_contracts`, `metadata`.
    - Indexes per DOC-014 § Indexing Strategy:
      - `trading_pairs`: `base_token_id` (separate index), `quote_token_id` (separate index) — "Which pairs exist for this token."
      - `tokens`: `contract_address` (for lookup by address).
      - `wallets`: `address` (for lookup by address).
    - CHECK constraints: `fee_tier_bps IS NULL OR fee_tier_bps BETWEEN 0 AND 10000`, `decimals >= 0 AND decimals <= 255`, `total_supply >= 0`.
    - Deps: step 10.
    - Verification: `make migrate` on fresh container; `\d tokens`, `\d trading_pairs` show correct schema.
    - Complexity: moderate.

12. **`src/onchain_platform/persistence/postgres/entity_repositories.py`** — Entity CRUD repositories.
    - `save_token(session, token: Token) -> bool` — upsert on `canonical_id` (ON CONFLICT DO NOTHING for new, ON CONFLICT DO UPDATE for metadata enrichment).
    - `save_trading_pair(session, pair: TradingPair) -> bool` — upsert on `canonical_id`.
    - `save_liquidity_pool(session, pool: LiquidityPool) -> bool` — upsert on `canonical_id`.
    - `save_wallet(session, wallet: Wallet) -> bool` — upsert on `canonical_id`, update `first_seen_at` only if new value is earlier (idempotent replay).
    - `save_metadata(session, metadata: Metadata) -> bool` — upsert on `entity_id`.
    - `get_token(session, canonical_id: str) -> Token | None`
    - `get_trading_pair(session, canonical_id: str) -> TradingPair | None`
    - `list_pairs_for_token(session, token_canonical_id: str) -> list[TradingPair]` — uses the `base_token_id` and `quote_token_id` indexes.
    - All functions: SQLAlchemyError → PersistenceError (DOC-013 § Exception Hierarchy).
    - Deps: steps 3–8, 10.
    - Verification: integration tests against real Postgres.
    - Complexity: moderate.

### Phase C: Domain Management Services

13. **`src/onchain_platform/domain_management/entity_resolution.py`** — Entity Resolution service.
    - `resolve_from_pair_created(session, fact: BlockchainFact) -> None`:
      - Extract `token0_address`, `token1_address`, `pair_address`, `dex` from `PairCreatedPayload`.
      - Upsert `Token` for token0 and token1 (canonical_id from `ids.py`, symbol/name/decimals/total_supply as stubs — "UNKNOWN" / "0" — until metadata enrichment).
      - Upsert `TradingPair` (canonical_id, base_token_id, quote_token_id, pool_address, creation_block, creation_fact_id).
      - Upsert `LiquidityPool` (canonical_id = pair's, protocol = dex, fee_tier_bps = None for V2).
      - Upsert `SmartContract` for the pool address (contract_type = POOL).
    - `resolve_from_swap_executed(session, fact: BlockchainFact) -> None`:
      - Extract `sender`, `recipient` from `SwapExecutedPayload`.
      - Upsert `Wallet` for sender and recipient (first_seen_at = min(existing, fact.event_time)).
    - All upserts are idempotent (ON CONFLICT DO NOTHING / DO UPDATE) — replay produces identical entity sets.
    - Deps: steps 1–8, 12.
    - Verification: unit tests with synthetic facts, integration tests against real Postgres.
    - Complexity: moderate.

14. **`src/onchain_platform/domain_management/wallet_service.py`** — Wallet Service.
    - Thin wrapper around `save_wallet` in entity_repositories. DOC-006 Ownership table: "Wallet Service" is distinct from "Entity Resolution." In M4, the distinction is organizational — both do upserts, but wallet_service owns the Wallet entity lifecycle.
    - `ensure_wallet(session, chain_id: int, address: str, first_seen_at: datetime) -> Wallet`
    - Deps: step 12.
    - Verification: unit test — first call creates, second call with later timestamp doesn't overwrite first_seen_at.
    - Complexity: trivial.

15. **`src/onchain_platform/domain_management/metadata_service.py`** — Stub Metadata Service.
    - `create_stub_metadata(session, entity_id: str) -> None` — inserts Metadata row with `verification_status=UNVERIFIED`, empty website/social_links/logo_url/description.
    - Called by entity_resolution after creating a new Token/TradingPair.
    - Deps: step 12.
    - Verification: unit test — creates metadata row with UNVERIFIED status.
    - Complexity: trivial.

### Phase D: Integration into Pipeline

16. **`src/onchain_platform/processing/fact_processor.py`** — Wire entity resolution.
    - After creating a `BlockchainFact`, call entity resolution synchronously:
      - For PAIR_CREATED: `entity_resolution.resolve_from_pair_created(session, fact)`
      - For SWAP_EXECUTED: `entity_resolution.resolve_from_swap_executed(session, fact)`
    - The fact processor now needs a session (passed in from the handler in main.py).
    - Entity resolution runs in the same session as fact persistence — atomic, no partial state.
    - Deps: step 13.
    - Verification: unit test — process a PairCreated CollectedLog, verify Token + TradingPair entities created.
    - Complexity: moderate.

17. **`src/onchain_platform/main.py`** — Wire entity resolution into the handler.
    - The handler in `_run_live` now calls entity resolution after saving the fact.
    - Deps: step 16.
    - Verification: `make lint`, `make typecheck`, `make import-check` pass.
    - Complexity: trivial.

### Phase E: Tests

18. **`tests/unit/test_entity_resolution.py`** — Unit tests for entity resolution.
    - `test_pair_created_creates_tokens_and_pair`: synthetic PairCreated fact → verify Token (x2) + TradingPair + LiquidityPool + SmartContract entities created with correct Canonical IDs.
    - `test_swap_executed_creates_wallets`: synthetic SwapExecuted fact → verify Wallet (x2) entities created with correct first_seen_at.
    - `test_entity_resolution_idempotent`: process same fact twice → no duplicate entities, same Canonical IDs.
    - `test_wallet_first_seen_at_not_overwritten_by_later_fact`: first fact at t=100, second at t=200 → first_seen_at stays 100.
    - Deps: steps 13–16.
    - Verification: `make test` green.
    - Complexity: moderate.

19. **`tests/integration/test_entity_resolution.py`** — Integration tests against real Postgres.
    - `test_pair_created_persists_entities_with_correct_canonical_ids`: real Postgres, insert PairCreated fact, verify entities in DB.
    - `test_swap_executed_persists_wallets`: real Postgres, insert SwapExecuted fact, verify wallets in DB.
    - `test_entity_resolution_idempotent_on_replay`: process same facts twice, verify entity count unchanged.
    - `test_list_pairs_for_token`: insert multiple pairs for same token, query by token canonical_id, verify correct results.
    - Deps: steps 12, 13.
    - Verification: `make test` green with compose up.
    - Complexity: moderate.

20. **`tests/replay/test_replay.py`** — Extend replay test with entity verification.
    - After replaying the fixture, verify that the expected Token and TradingPair entities exist in the DB with correct Canonical IDs.
    - Deps: steps 13, 16.
    - Verification: `make test-replay` green.
    - Complexity: moderate.

### Phase F: Final Gate

21. **Final gate + commit.**
    - `make lint && make typecheck && make import-check && make test && make test-replay` all green.
    - Update ImplementationPlan.md Milestone 4 DoD checkboxes.
    - Commit.

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Entity resolution creates duplicate entities on reorg replay | Low | High | All upserts use ON CONFLICT on `canonical_id` (the natural key). `canonical_id` is deterministic from address + chain_id — immutable across reorgs. Replay produces identical entity sets because upserts are idempotent. Unit test: process same fact twice, verify count unchanged. |
| Circular dependency: domain_management imports persistence, persistence imports domain | Low | High | DOC-011 § Enforcing the Dependency Rule: `domain_management` may import `persistence` (it's lower in the layers contract). `persistence` must NOT import `domain_management`. The entity_repositories.py file is in `persistence/` and accepts/returns domain types — no circular dependency. Import-linter enforces this mechanically. |
| Wallet service performance with high-volume swap facts | Low | Medium | Each SwapExecuted creates 2 wallet upserts. With ON CONFLICT DO NOTHING, the write cost is minimal for existing wallets. If performance becomes an issue at scale, batch upserts can be added later. Not a concern for MVP volume. |
| Canonical ID collision (impossible in EVM) | Very Low | High | EVM addresses are deterministic from deployment tx hash + sender nonce. Two different contracts cannot have the same address on the same chain. Document this guarantee in `ids.py` docstring. |
| Entity resolution lag causes FK violations in trading_pairs | Low | Medium | Entity resolution runs synchronously in the same session as fact persistence (step 16). Token entities are created BEFORE TradingPair (which references them via FK). Atomic session ensures no partial state. |
| Token stub data (symbol="UNKNOWN", decimals=0) misleads downstream consumers | Medium | Low | Stub data is clearly marked as placeholder. Metadata service creates UNVERIFIED metadata. Downstream consumers (M6 Features, M9 API) should check verification_status before using metadata. Document this contract. |
| `domain/enums.py` vs `domain/schemas/enums.py` confusion | Low | Low | DOC-011 v1.5 already resolved this: `schemas/enums.py` has fact-lifecycle enums (ConfirmationStatus, FactType), `domain/enums.py` has structural enums (ContractType, VerificationStatus). Clear docstring in each file. |

---

## 4. Definition of Done Matrix

| DoD Item (ImplementationPlan § Milestone 4) | Verification Method | Automated? |
|---|---|---|
| Every TradingPair from M1–M3 facts has a resolved Token on both sides | Integration test: replay fixture, query `trading_pairs` table, verify `base_token_id` and `quote_token_id` reference valid `tokens.canonical_id` rows | Yes |
| Stable Canonical ID for every entity | Unit test: `canonical_id` construction matches DOC-012 format for Token, TradingPair, LiquidityPool, Wallet | Yes |
| Entity resolution is idempotent | Unit test + integration test: process same facts twice, verify no duplicate entities, same Canonical IDs | Yes |
| `lint-imports` still passes | `make import-check` | Yes |
| Entity resolution runs synchronously within fact processor | Code review: entity_resolution calls are inside the fact processor's session, not async/separate | Manual |
| Metadata stub returns UNVERIFIED | Unit test: metadata_service creates row with verification_status=UNVERIFIED | Yes |
| FK constraints enforce referential integrity | Integration test: attempt to create TradingPair with invalid base_token_id → FK violation | Yes |

---

## 5. Out-of-Scope Confirmation

Per ImplementationPlan § Milestone 4 and § What Not To Build Yet:

- [x] Real metadata provider integration — NOT built. Stub only (UNVERIFIED for everything).
- [x] Wallet behavior analysis / Feature Engineering — NOT built. `domain_management/wallet_service.py` only creates Wallet entities with first_seen_at. No tagging, no behavior analysis (M6).
- [x] Smart contract verification status — NOT built. `is_verified` defaults to `False`. Real verification deferred.
- [x] Token logo enrichment — NOT built. `logo_url` is None in stub metadata.
- [x] API endpoints — NOT built. `research/` stays empty (M9).
- [x] Dashboard updates — NOT built (M9).
- [x] State Projection / Observation Snapshots — NOT built (M5).
- [x] Feature Engineering — NOT built (M6).
- [x] Outcome Generation — NOT built (M8).
- [x] No Redis Streams integration — direct function call pattern continues.
- [x] No second chain beyond Base.
- [x] No `utils/` or `common/` package.
- [x] No UUID entity IDs — Canonical IDs (natural keys) continue.

---

## 6. Questions / Blockers

Q1 (needs human): The `Token` entity schema has `symbol`, `name`, `decimals`, `total_supply` as required fields (DOC-012 Part A). With stub entity resolution, these will be placeholder values ("UNKNOWN", 0, "0"). Should the schema allow these to be None initially (and be filled in later by metadata enrichment), or should they always be required with placeholder values? Recommendation: required with placeholder values — the schema is the contract, and None would require Optional types everywhere. The stub values are clearly temporary and will be overwritten when real metadata arrives.

Q2 (design note): The `LiquidityPool.canonical_id` is "Same as parent TradingPair.canonical_id" (DOC-012 Part A). This means `liquidity_pools.canonical_id` is both its PK and an FK to `trading_pairs.canonical_id`. This is intentional — a Liquidity Pool has no identity independent of its pair in the MVP. Flagging so the migration handles this correctly (PK + FK on same column).

Q3 (design note): The `SmartContract` entity is created for the pool address when a PairCreated fact is processed. But the pool address is also the `TradingPair.pool_address` and `LiquidityPool.canonical_id`. There's no FK between SmartContract and TradingPair (SmartContract.address is not a unique key — multiple entities can share an address if they're different contract types). This is fine — SmartContract is a separate registry, not a parent of TradingPair.

Q4 (needs human): Should entity resolution also create `SmartContract` entities for token addresses (contract_type = ERC20)? The current plan only creates SmartContract for pool addresses. Token addresses are already tracked via the Token entity. Recommendation: yes, create SmartContract for token addresses too (contract_type = ERC20) — it's cheap and completes the contract registry. But defer to a follow-up if it adds scope.

No hard blockers: every Q above has a stated fallback that keeps M4 buildable today.
