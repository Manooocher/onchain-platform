# Provider Configuration Guide

How to configure multi-provider RPC access for the Base chain (ADR-006 §
Provider Abstraction). Providers are configured in `config/providers.yaml`;
API keys are read from environment variables (never stored in files).

## Supported Providers

### Alchemy (Recommended Primary)
- **Website:** https://www.alchemy.com
- **Rate limit:** 660 req/s (config)
- **Setup:**
  1. Create an account and a Base app.
  2. Copy the app's API key.
  3. Set `ALCHEMY_BASE_API_KEY` in `.env`.
- WebSocket support: yes (`ws_template` in config).

### QuickNode (Secondary)
- **Website:** https://www.quicknode.com
- **Rate limit:** 50 req/s (config)
- **Setup:**
  1. Create a Base endpoint.
  2. Copy the endpoint URL's subdomain + key (`https://<subdomain>.base-mainnet.quiknode.pro/<key>/`).
  3. Set `QUICKNODE_BASE_SUBDOMAIN` and `QUICKNODE_BASE_API_KEY`.
- WebSocket: not used by the poll-based collector in this config.

### RockX / W3Node (Tertiary)
- **Website:** https://rockx.com / https://w3node.com
- **Rate limit:** 25 req/s (config)
- **Setup:**
  1. Create a Base endpoint (`https://base.w3node.com/<key>/api`).
  2. Set `ROCKX_BASE_API_KEY`.
  3. **Note:** this provider's TLS posture in some environments requires
     disabling cert verification (`requires_ssl_no_revoke: true`). It is an
     explicit, documented trade-off scoped to this provider class — use as a
     last-resort failover only.
- **Important:** the Reality Check found the historical `base.gateway.rockx.com`
  hostname invalid, and a provided W3Node key returned 401. Confirm your key /
  endpoint against `eth_blockNumber` before relying on this provider; the
  orchestrator will route around it if it fails.

## Failover Strategy

The platform uses **priority-based failover with health checking**:

1. Try the primary provider first.
2. On transient failure, apply exponential backoff (1s, 2s, 4s).
3. After `unhealthy_threshold` (default 3) consecutive failures, mark the
   provider unhealthy.
4. Try the next provider in priority order (skipping unhealthy ones).
5. After `recovery_interval_seconds` (default 300), re-attempt an unhealthy
   provider.

Health-check interval, threshold, and recovery interval are set under the
`failover:` block in `config/providers.yaml`.

## Rate Limiting

Each provider owns a token-bucket limiter (`rate_limit_per_second`, with a 2×
burst capacity). The orchestrator also applies bounded backoff on 429s, so a
provider is never driven past its limit.

## Adding a Chain or Provider

Add a top-level `chain:` block in `config/providers.yaml`, listing `primary`,
`secondary`, `tertiary`, and `failover`. Provider URLs are templates with
`{api_key}` / `{subdomain}` placeholders resolved from env vars. Then select it
at startup:

```bash
uv run python -m onchain_platform.main --chain base
```

## Cost / Security Notes

- Monitor daily request volume against provider quotas.
- **Rotate any API key that is ever committed or disclosed.** The keys in
  `.env.example` are development/test keys; treat `.env` as the secret store.
- Disabling TLS verification (RockNode) is last-resort — prefer a correctly
  provisioned endpoint.