"""GoPlus Security API client (ADR-006 Principle 5: Buy Commodities).

HTTPX-based async client with:
- 30s timeout per call (DOC-013 § Async Conventions)
- Token bucket rate limiter (140 CU/min, 28,000 CU/day)
- 24h Redis caching for semi-static contract security data
- Exponential backoff on 429/5xx (1s, 2s, 4s, max 3 retries)
- All httpx errors → AcquisitionError (DOC-013 § Exception Hierarchy)

GoPlus Token Security API:
GET https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}

Free tier: 150 CU/min, 30,000 CU/day, 150,000 CU/month, no batch calls.
"""

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import redis.asyncio as redis
import structlog

from onchain_platform.domain.exceptions import AcquisitionError

logger = structlog.get_logger(__name__)

# Rate limiting: 140 CU/min capacity (margin under 150 limit).
_RATE_LIMIT_CAPACITY = 140
_RATE_LIMIT_REFILL_PER_SEC = _RATE_LIMIT_CAPACITY / 60.0  # ~2.33 tokens/sec
# Daily quota: 28,000 CU (margin under 30,000 limit).
_DAILY_QUOTA = 28_000
# Cache TTL: 24h (GoPlus contract security data is semi-static).
_CACHE_TTL_SECONDS = 24 * 60 * 60
# Retry config.
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.0
_TIMEOUT_SECONDS = 30.0


class GoPlusClient:
    """Async GoPlus Security API client.

    Dependencies injected (DOC-013 § Dependency & Composition):
    redis_client for caching and rate limiting.
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        base_url: str = "https://api.gopluslabs.io",
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_token_security(self, chain_id: int, address: str) -> dict[str, Any] | None:
        """Fetch token security data from GoPlus.

        Returns parsed result dict, or None if the token has no data.
        Cached in Redis for 24h. Rate-limited via token bucket.
        Daily CU tracked; stops at 28,000/day.
        """
        address_lower = address.lower()
        cache_key = f"goplus:{chain_id}:{address_lower}"

        # Check cache first.
        cached = await self._redis.get(cache_key)
        if cached is not None:
            logger.debug("goplus_cache_hit", chain_id=chain_id, address=address_lower)
            return dict(json.loads(cached))

        # Check daily quota.
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        cu_key = f"goplus_daily_cu:{today}"
        cu_used_raw = await self._redis.get(cu_key)
        cu_used = int(cu_used_raw) if cu_used_raw else 0
        if cu_used >= _DAILY_QUOTA:
            logger.warning(
                "goplus_daily_quota_exceeded",
                chain_id=chain_id,
                cu_used=cu_used,
                quota=_DAILY_QUOTA,
            )
            return None

        # Rate limit: token bucket in Redis.
        await self._acquire_token()

        # Make the API call with retries.
        result = await self._call_with_retry(chain_id, address_lower)

        # Track CU usage.
        await self._redis.incr(cu_key)
        await self._redis.expire(cu_key, 86400)  # 24h TTL

        # Cache the result for 24h.
        if result is not None:
            await self._redis.set(cache_key, json.dumps(result), ex=_CACHE_TTL_SECONDS)

        return result

    async def _acquire_token(self) -> None:
        """Token bucket rate limiter in Redis.

        140 tokens/min capacity, refilled at ~2.33 tokens/sec.
        Blocks until a token is available (max 60s wait).
        """
        bucket_key = "goplus_rate_limit"
        for _ in range(60):  # max 60 iterations = 60s wait
            now = time.monotonic()
            # Lua script for atomic token bucket.
            lua_script = """
            local key = KEYS[1]
            local capacity = tonumber(ARGV[1])
            local refill_rate = tonumber(ARGV[2])
            local now = tonumber(ARGV[3])

            local data = redis.call('HMGET', key, 'tokens', 'last_refill')
            local tokens = tonumber(data[1]) or capacity
            local last_refill = tonumber(data[2]) or now

            local elapsed = now - last_refill
            tokens = math.min(capacity, tokens + elapsed * refill_rate)

            if tokens >= 1 then
                tokens = tokens - 1
                redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
                redis.call('EXPIRE', key, 120)
                return 1
            else
                redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
                redis.call('EXPIRE', key, 120)
                return 0
            end
            """
            result = await self._redis.eval(
                lua_script,
                1,
                bucket_key,
                str(_RATE_LIMIT_CAPACITY),
                str(_RATE_LIMIT_REFILL_PER_SEC),
                str(now),
            )
            if result == 1:
                return
            await asyncio.sleep(1.0)

        raise AcquisitionError("GoPlus rate limiter: no token available after 60s")

    async def _call_with_retry(self, chain_id: int, address: str) -> dict[str, Any] | None:
        """Make the API call with exponential backoff on 429/5xx."""
        url = f"{self._base_url}/api/v1/token_security/{chain_id}"
        params = {"contract_addresses": address}

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_client()
                response = await client.get(url, params=params)

                if response.status_code == 429:
                    # Rate limited — back off.
                    wait = _BACKOFF_BASE_SECONDS * (2**attempt)
                    logger.warning(
                        "goplus_rate_limited",
                        chain_id=chain_id,
                        address=address,
                        attempt=attempt,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 500:
                    # Server error — back off.
                    wait = _BACKOFF_BASE_SECONDS * (2**attempt)
                    logger.warning(
                        "goplus_server_error",
                        chain_id=chain_id,
                        address=address,
                        status=response.status_code,
                        attempt=attempt,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                response.raise_for_status()
                body = response.json()

                if body.get("code") != 1:
                    logger.warning(
                        "goplus_api_error",
                        chain_id=chain_id,
                        address=address,
                        message=body.get("message"),
                    )
                    return None

                result = body.get("result", {})
                # Result is keyed by lowercase address.
                token_data = result.get(address.lower())
                if token_data is None:
                    logger.info(
                        "goplus_no_data",
                        chain_id=chain_id,
                        address=address,
                    )
                    return None

                return dict(token_data)

            except httpx.TimeoutException as exc:
                last_error = exc
                wait = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "goplus_timeout",
                    chain_id=chain_id,
                    address=address,
                    attempt=attempt,
                    wait_seconds=wait,
                )
                await asyncio.sleep(wait)

            except httpx.HTTPError as exc:
                raise AcquisitionError(
                    f"GoPlus HTTP error for {address} on chain {chain_id}: {exc}"
                ) from exc

        raise AcquisitionError(
            f"GoPlus request failed after {_MAX_RETRIES} retries for {address} "
            f"on chain {chain_id}: {last_error}"
        )
