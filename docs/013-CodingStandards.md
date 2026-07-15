---
id: DOC-013
title: Coding Standards
version: 1.2
status: Draft
owner: CTO
last_updated: 2026-07-13
tags:
  - coding-standards
  - engineering
  - conventions
  - implementation-policy
related_docs:
  - DOC-001 Vision
  - DOC-006 Domain Model
  - DOC-007 Data Flow
  - DOC-008 Canonical Glossary
  - DOC-010 Technology Stack
  - DOC-011 Repository Structure
  - DOC-012 Canonical Schema Specification
  - DOC-014 Persistence Policy
  - ADR-006 Blockchain Data Acquisition Strategy
---

# Coding Standards

> Standards are not style preferences. They are the difference between a rule this project already agreed to — in DOC-006, DOC-008, ADR-006 — and a rule the codebase actually obeys.

---

# Purpose

DOC-010 chose the tools. DOC-011 decided where code lives and which package may import which. DOC-012 fixed the exact shape of every Canonical Schema field. None of the three says how to *write the code inside those boundaries* — and several words that appear constantly across this project's documents (**deterministic**, **immutable**, **reproducible**, **explainable**) have never been translated into an actual rule a reviewer can check a pull request against. That translation is this document's only job.

**Scope boundary, stated once so it never needs restating:** if a rule is about *which* technology to use, it belongs in DOC-010. If it's about *where* a file lives or *which package may import which*, it belongs in DOC-011. If it's about the *exact shape* of a Canonical Schema field, it belongs in DOC-012. This document governs everything else: the code inside a file, inside a boundary DOC-011 already drew.

Three of the nine sections below — Exception Hierarchy, Immutability & State Modeling, Determinism Discipline — are ordered first deliberately. They are the sections where answering late is expensive: exactly like `Feature.value`'s type and the discriminated-union payload shape in DOC-012, a wrong or missing answer here doesn't stay a documentation gap, it becomes code that has to be refactored once the gap is finally noticed.

---

# Exception Hierarchy

The same discipline DOC-011 applies to types crossing a Capability boundary — "everything crossing that boundary is a Canonical Schema" — applies to *exceptions* too, and nothing currently says so. An `IntegrityError` from SQLAlchemy or a `TimeoutError` from `httpx` is exactly as much an infrastructure type as an ORM model; it must not cross a Capability boundary unwrapped.

**Rule:** any module sitting at a Capability boundary that catches an infrastructure-specific exception must translate it into a `PlatformError` subclass before it propagates further. Concretely, `persistence/` translates every SQLAlchemy exception it catches; `acquisition/providers/` translates every provider/RPC-level exception (timeout, rate limit, malformed response) before it leaves that package.

The hierarchy lives in `domain/exceptions.py` — alongside `schemas/`, `entities/`, `enums.py`, and `ids.py`, because like those, it is a shared contract every layer needs, not a Capability-owned concern:

```python
class PlatformError(Exception):
    """Base for every exception raised across a Capability boundary.
    A bare Exception, SQLAlchemyError, or httpx error crossing a
    boundary un-translated is a bug in the module that let it through."""

class DomainValidationError(PlatformError):
    """A business rule was violated after Pydantic's own field-level
    validation already passed — e.g. a liquidity_delta sign that
    contradicts its fact_type (DOC-012 § E). Deliberately NOT named
    ValidationError: pydantic.ValidationError already owns that name,
    and a same-named sibling class is a guaranteed source of wrong
    except clauses and shadowed imports."""

class PersistenceError(PlatformError):
    """Raised at the persistence/ boundary. Includes the row-level
    immutability guard in facts.py — see § Immutability & State
    Modeling."""

class AcquisitionError(PlatformError):
    """Raised at the acquisition/ boundary — RPC timeout, rate limit,
    malformed provider payload. Never a raw httpx or provider-SDK
    exception past this point."""

class SchemaVersionError(PlatformError):
    """The Schema Version Dispatcher (DOC-010 § Data Processing)
    received a schema_version it has no parser for."""

class TransportError(PlatformError):
    """Raised at the transport/ boundary — Redis connection loss,
    Stream write failure, or Consumer Group error. Never a raw
    aioredis/redis-py exception past this point."""

```

**Domain Events vs. Exceptions:** Reorgs are modeled as Domain Events (e.g., `ChainReorgDetected` containing fork block and depth) published to Redis Streams, never as Exceptions. Exceptions are reserved for true failures (e.g., timeouts, validation errors). Using exceptions for routine control flow like a reorg is an anti-pattern that breaks asynchronous event-driven pipelines.

Five subclasses, not one per Capability — a `forbidden` contract per package (DOC-011) is warranted because import cycles are a structural risk; an exception subclass per package is not, until a Capability shows it actually needs to distinguish its own failure modes from a sibling's.

---

# Immutability & State Modeling

DOC-006 states that *"Only Finalized Facts become immutable."* Read literally, that sentence is answering two different questions at once, and conflating them is exactly what produces the wrong fix (a `PendingFact`/`FinalizedFact` type split that the Confirmation Lifecycle doesn't actually need). They are separated here for good.

## Object-level immutability (Python) — applies to every Canonical Schema, unconditionally

Every Pydantic model under `domain/schemas/` **and** `domain/entities/` sets `model_config = ConfigDict(frozen=True)`. This is not scoped to `BlockchainFact`, and not conditioned on `confirmation_status`: a `Pending` fact is exactly as frozen an object as a `Finalized` one, and so is a `Token` entity whose metadata will legitimately be enriched next week. State change is never mutation — it is always `model_copy(update={...})`, producing a new object.

This single rule removes any need for parallel type hierarchies to represent lifecycle stages. It also means: **whether the underlying storage row is update-in-place or append-only is entirely a persistence-layer question** (the next subsection), completely orthogonal to whether the in-memory Python object is frozen. A `Checkpoint` (DOC-012 § B.0, a mutable singleton row) and a `BlockchainFact` (§ B.1, append-only) are both frozen Python objects — the difference between them lives only in what `persistence/` is allowed to do with the row behind each.

One practical consequence worth stating explicitly: after `model_copy`, the *old* object is now stale. Nothing enforces that a caller discards it — a frozen object is still a valid Python reference. Code that holds a `Fact` across an `await` boundary while its `confirmation_status` might change underneath it should re-fetch, not assume the reference it's holding is current.

**Pydantic validators must be pure functions.** `@field_validator` and `@model_validator` may perform type coercion, format normalization (e.g., lowercasing an address), and business invariants (e.g., `liquidity_delta > 0`). They must **never** perform I/O (no API calls, no database queries, no file reads) or depend on wall-clock time. I/O belongs in the Capability layer, never inside the Schema itself.


## Row-level immutability (PostgreSQL) — this is what DOC-006's sentence actually means

Once a row lands with `confirmation_status = FINALIZED`, no `UPDATE` may ever touch it again. This is enforced in exactly one place: `persistence/postgres/facts.py`, as a guard evaluated before any `UPDATE` statement against the Facts table. A violation raises `PersistenceError` (§ Exception Hierarchy), not a silent no-op — a silent no-op here would hide a bug that a Replay Test would otherwise catch.

`Checkpoint` (§ B.0) is the deliberate counterexample: it is a singleton row per `chain_id`, and it is *supposed* to be overwritten in place as ingestion advances. The row-level guard applies to `BlockchainFact` rows specifically, keyed on `confirmation_status`, not to every table `persistence/` touches.

A Postgres `CHECK` constraint or `BEFORE UPDATE` trigger as defense-in-depth beyond the application-level guard is a reasonable future addition, not a requirement for the MVP — the guard in `facts.py` is the primary and sufficient enforcement for now.

---

# Determinism Discipline

"Deterministic" appears in DOC-001, DOC-006, DOC-007, DOC-008, and DOC-009. None of the five says how to actually write deterministic Python. Three rules close that gap.

**No wall-clock inside Capability logic.** `datetime.now()` and `time.time()` never appear inside `acquisition/`, `processing/`, `domain_management/`, `analytics/`, `intelligence/`, `research/`, or `strategy/` business logic. Time is always a parameter, or read from a Canonical Schema field (`event_time`, `observed_at`, `ingested_at` — DOC-008 § Triple Timestamp Standard). `main.py` and `platform/` are the only places a clock may be read directly, and only to *produce* one of those three timestamps at the moment data enters the system — never to *compute with* inside a Capability.

**Ordered iteration only, on any path that produces an aggregate or hash.** Python `dict` has guaranteed insertion-order iteration since 3.7 — that part is not the risk. `set` is: its iteration order depends on hash randomization (`PYTHONHASHSEED`) and is not guaranteed stable across processes or Python versions. Any code building a Market Bar, a Feature, or anything that will be compared byte-for-byte in a Replay Test must not iterate a `set` on that path — collect into a `list` (or a `dict` whose insertion order is itself deterministic, e.g. sorted by `fact_id`) before aggregating.

**Polars parallel float aggregation and Replay Tests.** Polars aggregations must execute multi-threaded (the default) to preserve SIMD and parallelization advantages. Because floating-point math across multiple threads yields different round-offs depending on accumulation order, **byte-identical replay tests for `float` values are strictly forbidden.** The only two fields DOC-012 actually types as native `float` are `Feature.value` and `Blockchain.avg_block_time_seconds` — nothing else qualifies, and in particular `MarketBar`'s OHLCV fields (`open`/`high`/`low`/`close`/`volume`/`vwap`) do **not**: DOC-012 § B.3 types every one of them `str` (Decimal-as-string), so they remain byte-identical, zero-tolerance, like every other Financial Precision field. Replay Tests verifying the two genuine `float` fields must use a mathematical tolerance (e.g., `assert abs(a - b) < 1e-10`) — see DOC-010 § Testing for the one-line summary of this same rule. Strict byte-for-byte equality is reserved exclusively for `Decimal`, `String`, and the structural presence/absence of records.

**No unseeded randomness in pipeline logic.** This one is already true in practice — every identifier in DOC-012 (`fact_id`, `snapshot_id`, `bar_id`, `feature_id`) is a natural composite key, never `uuid.uuid4()` — but it has never been stated as a rule a reviewer can point to. Stated now: `random` and unseeded UUID generation do not appear anywhere between an Event entering `acquisition/` and a Feature or Outcome leaving `analytics/`.

**No `Any` in Capability Interfaces.** `mypy` must be run with `--strict` (or `disallow_untyped_defs = True` in `mypy.ini`). The use of `typing.Any` in a public function signature or a Canonical Schema is a lint error. If a third-party library returns an untyped response, the Capability boundary must immediately cast/wrap it into a typed Pydantic model before passing it deeper into the system.
---

# Dependency & Composition

DOC-011's `forbidden` contracts stop `persistence/`, `transport/`, and `platform/` from importing a Capability package. Nothing stops the *reverse*: a Capability module importing a configured global straight out of `platform/config.py`. That is the same implicit coupling the import-linter contracts exist to prevent — it has just moved from Capability-to-Capability to Capability-to-infrastructure, where nothing is currently watching for it.

**Rule:** `Settings` (from `platform/config.py`) and database sessions are always passed in — as a constructor argument or an explicit function parameter — never imported as a live, already-configured object from inside a Capability module. `main.py` (§ Composition Root, DOC-011) constructs one `Settings` instance and one session factory at startup and threads them through everything else. This is manual, explicit wiring, not a DI framework — nothing in DOC-010 selects one, and none is needed at this scale. If wiring grows unmanageable, that is itself a Migration Trigger worth adding to DOC-010, not a reason to start importing globals in the meantime.

**Logging is the deliberate exception to this rule, not an oversight.** `structlog.get_logger(__name__)` called at module scope inside a Capability is fine. The reason it's different from `Settings` or a DB session: a logger obtained this way carries no business-relevant state that varies per call or per test — it's a name-bound handle onto behavior that `platform/logging.py` configures exactly once, in `main.py`, at process startup (`structlog.configure(...)`). Test log capture works by intercepting that global processor chain, not by injecting a different logger instance per call. Forcing logger injection everywhere would add real boilerplate for a problem this specific case doesn't actually have.

**Database sessions are scoped to function calls or request contexts, never stored as instance attributes on long-lived objects.** A SQLAlchemy async session must be passed as an argument to the function that needs it (or injected via FastAPI's `Depends`), used, and then closed. Storing a session in `self.session` on a service class that persists across `await` boundaries is a guaranteed source of async deadlocks.

---

# Observability in Code

DOC-001 names "Observable" as a design principle — *"every important decision, event, and transformation should be traceable"* — without saying what a log line actually needs to contain to make that true. A single global list of mandatory fields would be wrong here, because different Capabilities are traceable through different keys:

| Capability | Mandatory structured fields on every log line |
| --- | --- |
| `acquisition/`, `processing/` | `chain_id`, `block_number`, `tx_hash` (when applicable) |
| `domain_management/` | `entity_id` (Canonical ID, DOC-008) |
| `analytics/` | `entity_id`, `as_of_timestamp` |
| `intelligence/` | `entity_id`, and either `risk_rule_id` or `insight_type` |
| `research/`, `strategy/` | a request-scoped `request_id` |

**Enforcement:** A custom structlog processor will be implemented in `platform/logging.py` that raises a `lint` warning (or fails CI in strict mode) if a log entry originates from a Capability module without its mandatory structured fields attached. Developers must use bound loggers (e.g., `logger.bind(chain_id=..., block_number=...)`) at the entry point of each Capability pipeline to ensure downstream logs inherit the required context.

**Log level policy:** DEBUG for per-event pipeline tracing; INFO for lifecycle transitions (a Fact finalized, a Bar computed, a Checkpoint advanced); WARNING for degradation the platform recovered from on its own (DOC-007's *"graceful degradation is preferred over cascading failures"* has to show up as an actual log level somewhere, and this is it); ERROR only for something that needs a human. A successfully-handled `ChainReorgDetected` event is logged at INFO or WARNING, never ERROR — it is the Confirmation Lifecycle working as designed, not a failure of it.

**Never log secrets.** RPC API keys, provider credentials — this is DOC-010 § Security's rule, restated here because it is a code-level habit (never `logger.info("connecting", url=rpc_url_with_key)`), not just a configuration-file rule.

---

# "Why, With a Reference"

Nearly every file-level comment in DOC-011 cites a governing section — `"DOC-012 § B.1"`, `"ADR-006 § Checkpointing"` — rather than just describing what the code does. That habit is deliberate, has held up well across three revisions of this document set, and applies just as much inside the code itself, not only in architecture documents.

**Rule:** any comment attached to non-obvious business logic — a rule that isn't self-evident from the code alone — cites the document and section that governs it, on the same line or the line directly above.

```python
# BAD — describes what, not why
# skip facts older than the window
if fact.event_time < window_start:
    continue

# GOOD — the reference is the point
# Point-in-Time Correctness (DOC-008 § D): a Feature must never see
# a Fact outside its observation window, regardless of when the Fact
# was ingested.
if fact.event_time < window_start:
    continue

```

A comment that only restates the code adds nothing a reader couldn't get from the code itself. A comment that names the rule being protected turns the same line into something a reviewer — human or AI agent — can actually verify against the source of truth.

---

# Testing Conventions

DOC-011 already fixes *where* tests live (`unit/`, `integration/`, `replay/`, `schema/`). This section fixes how they're written inside those directories.

**Naming:** `test_<unit>_<scenario>_<expected_outcome>` — e.g. `test_finality_engine_reorg_below_confirmation_depth_marks_orphaned`. A test name that doesn't state the expected outcome is a test whose failure message will need a debugger to interpret.

**Canonical Schema factories.** Hand-writing a `BlockchainFact(...)` or `Feature(...)` literal inside a test is exactly the place a `0.1` sneaks in where a `Decimal("0.1")` belongs — reintroducing, inside the test suite, the precise bug the Financial Precision Principle (DOC-008) exists to prevent everywhere else. A small `tests/factories/` module — one builder function per Canonical Schema, every Decimal field defaulted correctly — is a natural addition to the `tests/` tree DOC-011 already defines, not a change to it; DOC-011 states new leaf packages should not require reshaping the structure to add, and this is exactly that case.

**Mocking boundary.** Integration tests run against real Postgres and Redis (DOC-011 already establishes this — "not mocks"). Unit tests may mock, but only at a Capability's `public`-facing interface or an ADR-006 Provider abstraction — never an internal implementation two layers deep (a SQLAlchemy session, a Polars DataFrame mid-pipeline). If a unit test needs to reach that deep to pass, the test is describing an implementation detail, not a contract, and the Capability boundary it's crossing is the same one § Dependency & Composition and DOC-011's `forbidden` contracts already protect.

---

# Async Conventions

Python's `asyncio` runs through every layer of this stack (DOC-010 § Runtime). Three rules keep that coherent rather than accidental:

**No blocking calls inside `async def`.** No synchronous `requests`, no unwrapped synchronous file I/O, no `time.sleep` — use `await asyncio.sleep(...)`, and wrap unavoidable blocking calls in `asyncio.to_thread(...)`. A single blocking call inside an `async def` stalls the entire event loop, not just its own coroutine.

**Every external call carries an explicit timeout.** RPC calls, WebSocket connects, HTTPX requests to commodity providers (DOC-010 § Blockchain Connectivity) — none of these may rely on a library default. An RPC provider that hangs instead of erroring is exactly the failure mode `BlockchainProvider` failover (ADR-006 § Provider Abstraction) exists to route around, and failover can't trigger on a call that never returns.

**Collectors shut down gracefully.** On `SIGTERM`/`SIGINT`, `acquisition/collector.py` finishes processing whatever event is currently in flight and only then allows the Checkpoint to reflect that state, before exiting. Killing the process mid-event and leaving the Checkpoint pointing past a partially-processed block turns an ordinary restart into a Recovery Procedure (ADR-006 § Checkpointing) it didn't need to be.

---

# Definition of Done

A pull request is not ready for review until every applicable line below is true. This is the checklist every other section in this document exists to make checkable.

* [ ] Ruff and mypy pass (DOC-010 § Testing).
* [ ] `make import-check` passes — no `forbidden` or `layers` contract violation (DOC-011 § Enforcing the Dependency Rule).
* [ ] Any new exception raised across a Capability boundary is a `PlatformError` subclass (§ Exception Hierarchy), not a raw infrastructure exception.
* [ ] Any new Canonical Schema field follows DOC-012's Decimal/float rule, not a local judgment call.
* [ ] Any new or modified logic touching `BlockchainFact` includes a Replay Test, or an explanation in the PR description of why one doesn't apply.
* [ ] Any new Feature name has a suffix from DOC-012 § Feature Naming Convention.
* [ ] Any code that reads the wall clock, iterates a `set` on an aggregation path, or introduces unseeded randomness inside a Capability is flagged and justified in review, not merged silently (§ Determinism Discipline).
* [ ] No `Settings` or DB session is imported as a configured global from inside a Capability module (§ Dependency & Composition).
* [ ] Log lines touching a Fact, Entity, or Feature carry the mandatory fields for that Capability (§ Observability in Code).
* [ ] Non-obvious logic cites the document and section it implements (§ "Why, With a Reference").

---

# Guiding Principle

> A standard nobody can check against a diff is a hope, not a standard.
> Every rule above exists because a document elsewhere in this project already made a promise — deterministic, immutable, reproducible, explainable — and code is where that promise is either kept or quietly broken.

```