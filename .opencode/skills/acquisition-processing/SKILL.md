---
name: acquisition-processing
description: Use when writing or modifying anything under src/onchain_platform/acquisition/** or src/onchain_platform/processing/** — the highest correctness bar in this repo. Also use when reasoning about reorgs, finality, or checkpoints anywhere in the codebase. Full spec: docs/adr/ADR-006-Blockchain-Data-Acquisition-Strategy.md.
---

# Acquisition & Processing — the highest correctness bar in this repo

This is where Point-in-Time Correctness holds or breaks for everything downstream — treat changes here with more scrutiny than anywhere else in the codebase.

- `acquisition/checkpoint.py` is read-only. Only `processing/finality_engine.py` may advance a Checkpoint — it's the only code that knows a block is actually finalized.
- Business logic never depends on a provider SDK directly, only on `acquisition/providers/base.py`'s `BlockchainProvider` interface. `main.py` is the only place a vendor name (Alchemy, QuickNode, ...) should appear outside `acquisition/providers/`.
- `finality_engine.py` validates canonical chain continuity across the *full configured confirmation window* on every new block, not just the latest block's parent hash — that's what catches multi-block reorgs, not only single-block ones (ADR-006 § Canonical Chain Validation Engine).
- On a detected reorg: mark the affected `blockchain_facts` range `ORPHANED`, publish a `ChainReorgEvent` (DOC-012 § B.5) via `transport/event_stream.py`. Never raise it as a Python exception — DOC-013 is explicit that this is routine control flow, not a failure.
- Redis is transport, not truth. If a message is lost before acknowledgement, the fix is replaying from the blockchain via the last Checkpoint — never assume Redis retained something durably.
- Any change here needs a Replay Test, not just a unit test. `make test-replay` before considering the change done.
