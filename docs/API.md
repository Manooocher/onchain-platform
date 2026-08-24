# API Reference

A practical usage guide for the read-only Research API. For the authoritative endpoint contract, see [DOC-015 API Contracts](015-APIContracts.md).

## Overview

- **Base URL:** `http://localhost:8000`
- **OpenAPI spec:** `http://localhost:8000/v1/openapi.json`
- **Interactive docs:** `http://localhost:8000/docs`
- **Authentication:** none for the MVP (documented deferral, DOC-015)
- **Rate limiting:** none for the MVP (single-user assumption)
- **Read-only:** every endpoint is `GET`

## Pagination

All collection endpoints use **cursor-based pagination** (never offset):

```json
{
  "items": [ ... ],
  "pagination": { "next_cursor": "...", "has_more": true }
}
```

- Default `limit`: 100; maximum: 1000 (clamped server-side).
- Follow `next_cursor` until `has_more` is false.

```bash
GET /v1/pairs?limit=100
GET /v1/pairs?limit=100&cursor=<next_cursor>
```

## Error Handling

Every error uses one shape, always with a `correlation_id`:

```json
{
  "error": { "code": "RESOURCE_NOT_FOUND", "message": "...", "correlation_id": "abc123" }
}
```

| Code | HTTP | Meaning |
|------|------|---------|
| `VALIDATION_ERROR` | 422 | Malformed query parameter |
| `RESOURCE_NOT_FOUND` | 404 | Resource / PIT value does not exist |
| `PLATFORM_ERROR` | 500 | A `PlatformError` (e.g. persistence) leaked to the boundary |
| `INTERNAL_ERROR` | 500 | Unexpected error |

## Endpoints

### Health

#### `GET /v1/health`
Liveness check (no DB dependency).

### Trading Pairs

#### `GET /v1/pairs`
List trading pairs. Filters: `chain_id`, `dex`, `created_after`, `cursor`, `limit`.

```bash
curl "http://localhost:8000/v1/pairs?chain_id=8453&limit=10"
```

#### `GET /v1/pairs/{pair_id}`
Pair detail with nested `LiquidityPool` + `Metadata`. `pair_id` is a URL-encoded canonical ID (`eip155:8453/pair:0xAb58...`).

#### `GET /v1/pairs/{pair_id}/bars`
OHLCV bars. Params: `interval` (required: `1m|5m|15m|1h`), `start`, `end`, `include_provisional` (default `false`), `cursor`, `limit`.

```bash
curl "http://localhost:8000/v1/pairs/<id>/bars?interval=1h&limit=24"
```

#### `GET /v1/pairs/{pair_id}/facts`
Raw facts for a pair. Params: `fact_type`, `start`, `end`, `include_unfinalized` (default `false`), `cursor`, `limit`.

#### `GET /v1/pairs/{pair_id}/dataset`
Assemble a research dataset (`pair` + `bars` + `features` + `outcomes`) in one call.
Params: `interval` (required), `start` (required), `end` (required, ≤90 days from start), `feature_names` (optional, comma-separated).

```bash
curl "http://localhost:8000/v1/pairs/<id>/dataset?interval=1h&start=2026-06-01T00:00:00Z&end=2026-06-08T00:00:00Z"
```

### Features (Point-in-Time)

#### `GET /v1/entities/{entity_id}/features/{feature_name}`
Most recent feature value as of `as_of` (defaults to now). **404** if no value satisfies the filter.

```bash
curl "http://localhost:8000/v1/entities/<id>/features/liquidity_growth_pct_1h?as_of=2026-06-01T00:00:00Z"
```

#### `GET /v1/entities/{entity_id}/features`
Every feature name for the entity, each resolved to its latest-as-of-`as_of` value.

### Outcomes / Insights / Snapshots

#### `GET /v1/entities/{entity_id}/outcomes`
Outcome history. Params: `outcome_type` (`RUG_PULL|SUCCESSFUL_LAUNCH|DEAD_TOKEN`), `cursor`, `limit`.

#### `GET /v1/entities/{entity_id}/insights`
Insight history. Params: `insight_type`, `start`, `end`, `cursor`, `limit`.

#### `GET /v1/entities/{entity_id}/snapshots`
Observation snapshot history. Params: `start`, `end`, `cursor`, `limit`.

### Wallets

#### `GET /v1/wallets/{wallet_id}`
Wallet detail.

#### `GET /v1/wallets/{wallet_id}/activity`
Facts involving the wallet. Params: `start`, `end`, `cursor`, `limit`.

### Strategy

#### `GET /v1/strategy/rankings`
Ranked candidates with explainable factors. Params: `chain_id`, `dex`, `limit` (default 50, max 100), `as_of`.

```bash
curl "http://localhost:8000/v1/strategy/rankings?chain_id=8453&limit=10"
```

## Python Client

### With the bundled dashboard client

```python
from onchain_platform.research.dashboard.api_client import OnchainPlatformClient

client = OnchainPlatformClient(base_url="http://localhost:8000")
pairs = client.get_pairs(chain_id=8453, limit=10)
dataset = client.get_dataset(
    pair_id="eip155:8453/pair:0xAb58...",
    interval="1h",
    start="2026-06-01T00:00:00Z",
    end="2026-06-08T00:00:00Z",
)
rankings = client.get_rankings(chain_id=8453, limit=10)
```

### With raw HTTPX

```python
import httpx

async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
    resp = await client.get("/v1/pairs", params={"chain_id": 8453, "limit": 10})
    pairs = resp.json()["items"]
```

## Best Practices

1. **Use `as_of`** for reproducible research — always specify a point-in-time for feature queries.
2. **Follow pagination** — loop until `has_more` is false.
3. **Prefer `/dataset`** over many calls when assembling features + bars + outcomes for a pair.
4. **Handle errors** — check non-200 responses and surface `error.correlation_id` in logs.
5. **Percent-encode canonical IDs** — they contain `/` and `:`, which must be URL-encoded in path segments.