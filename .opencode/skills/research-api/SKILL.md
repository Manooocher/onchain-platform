---
name: research-api
description: Use when writing or modifying anything under src/onchain_platform/research/**, or when a task proposes a new HTTP route, a response model, or pagination behavior for the API. Full spec: docs/015-APIContracts.md.
---

# Research API — read-only, agent-readable

- `GET` only. There is no write path from this API, anywhere. Writes only ever come from `acquisition/` → `processing/` internally — if a task description implies adding a `POST`/`PUT`/`DELETE` route here, stop and check DOC-015 § Purpose (pillar 1) before proceeding.
- No auth in the MVP (DOC-010 § Security). Don't add any without a dedicated ADR first — this is a deliberate, documented gap, not an oversight to quietly fix.
- A response model is the Canonical Schema itself, serialized directly from `domain/schemas/` or `domain/entities/`. Never introduce a parallel, hand-maintained API DTO that happens to resemble one.
- Collections paginate by cursor, never offset — every collection endpoint queries a table under continuous append from the ingestion pipeline. `/pairs/{id}/dataset` is the one exception: bounded by a required `start`/`end` range (max 90 days) instead of cursor pagination.
- Point-in-time queries (`?as_of=`) resolve to the `(entity_id, feature_name, as_of_timestamp DESC)` index — that index exists (DOC-014) because this exact query shape was designed in before the endpoint was written.
- `/pairs/{id}/dataset` returns `bars`, `features` (vertical, one row per `feature_name`/`as_of_timestamp` — never pivoted into columns), and `outcomes` (a plural array). It deliberately excludes raw `Facts` and `ObservationSnapshot`s — those have their own endpoints, and embedding them here would defeat the 90-day bound.
