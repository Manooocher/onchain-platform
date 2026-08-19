---
name: testing
description: Use when writing or modifying anything under tests/**, or when a change anywhere else needs a test written or updated. Full spec: docs/013-CodingStandards.md § Testing Conventions.
---

# Testing conventions

- `tests/unit/` mirrors `src/` package-for-package.
- `tests/integration/` runs against real Postgres/Redis (docker compose), never mocks — Collector → Fact Processor → Persistence, end to end.
- `tests/replay/` re-processes a fixed, committed historical fixture and asserts output against a stored baseline. `Decimal`/`str` fields must be byte-identical. `float` fields (`Feature.value` only) use a tolerance comparison — Polars parallel float aggregation is not guaranteed bit-identical across thread counts, and this project doesn't pretend otherwise.
- `tests/schema/` uses `hypothesis` property-based tests per Canonical Schema. Use the shared fixture factories — a hand-constructed `Decimal` inline in a test can quietly recreate the exact bug the Financial Precision Principle exists to prevent.
- Naming: `test_<unit>_<scenario>_<expected_outcome>`.
- `make test` intentionally excludes `tests/replay/` (slow, needs fixture data) — run `make test-replay` separately, always before a PR touching `processing/` or `analytics/`.
