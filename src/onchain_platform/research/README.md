# Research Platform

The human/agent-facing surface of the platform — a read-only FastAPI REST API and a Streamlit dashboard. See [DOC-015 API Contracts](../../docs/015-APIContracts.md) for the authoritative endpoint specification.

## Structure

```
research/
├── api/              # FastAPI REST API
│   ├── main.py       # App factory (create_app) + middleware + CORS
│   ├── routes/       # Endpoint handlers (one file per resource)
│   ├── pagination.py # Cursor encode/decode (base64-URL-safe)
│   ├── errors.py     # Shared error body + correlation_id
│   ├── schemas.py    # Pagination envelope + compound responses
│   └── deps.py       # get_session / get_settings
└── dashboard/        # Streamlit UI
    ├── app.py        # Main dashboard app
    ├── api_client.py # Typed HTTPX client (the ONLY data path)
    └── pages/        # Dashboard pages
```

## API Endpoints

See [DOC-015 API Contracts](../../docs/015-APIContracts.md) for full detail and [docs/API.md](../../docs/API.md) for a usage guide. Core endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /v1/health` | Liveness check |
| `GET /v1/pairs` | List trading pairs (cursor-paginated) |
| `GET /v1/pairs/{id}` | Pair details (nested LiquidityPool + Metadata) |
| `GET /v1/pairs/{id}/bars` | OHLCV history (`interval` required) |
| `GET /v1/pairs/{id}/facts` | Raw facts for a pair |
| `GET /v1/pairs/{id}/dataset` | Assembled research dataset |
| `GET /v1/entities/{id}/features[/{name}]` | Point-in-time feature query (`?as_of=`) |
| `GET /v1/entities/{id}/outcomes` | Outcome history |
| `GET /v1/entities/{id}/insights` | Insight history |
| `GET /v1/entities/{id}/snapshots` | Observation snapshot history |
| `GET /v1/wallets/{id}[/activity]` | Wallet + activity |
| `GET /v1/strategy/rankings` | Ranked candidates |

### Query Patterns

**Cursor-based pagination** (never offset):
```bash
GET /v1/pairs?limit=100
GET /v1/pairs?limit=100&cursor=<opaque>
```

**Point-in-time feature query:**
```bash
GET /v1/entities/eip155:8453/pair:0xabc.../features/liquidity_growth_pct_1h?as_of=2026-06-01T00:00:00Z
```

## Dashboard

Run the Streamlit dashboard (separate terminal from the API):

```bash
uv run streamlit run src/onchain_platform/research/dashboard/app.py
```

Access at: http://localhost:8501

### Dashboard Pages
1. **Top Candidates** — ranked opportunities (explainable factors)
2. **Pairs List** — browse and filter trading pairs
3. **Pair Detail** — bars, features, outcomes for one pair
4. **Dataset Explorer** — assemble a research dataset

## API Client

The dashboard reads **only** through `api_client.py` (HTTPX) — it never imports `persistence/` directly (DOC-015 § Dashboard: "never a second data path"). Use the same client in your own scripts:

```python
from onchain_platform.research.dashboard.api_client import OnchainPlatformClient

client = OnchainPlatformClient(base_url="http://localhost:8000")
pairs = client.get_pairs(chain_id=8453, limit=100)
features = client.get_features(
    "eip155:8453/pair:0xabc...",
    as_of="2026-06-01T00:00:00Z",
)
rankings = client.get_rankings(chain_id=8453, limit=10)
```