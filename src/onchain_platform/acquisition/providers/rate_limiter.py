"""Token-bucket rate limiter (per-provider; Reality Check Risk: 429 errors).

Prevents a provider from being hammered past its configured rate limit. Each
provider owns one limiter (rate_limit_per_second from config) with a burst
capacity of 2x the rate.

Determinism (DOC-013): uses `time.monotonic()` for elapsed-time refill, which
is a scheduler/measurement concern appropriate to a transport limiter (not a
capability computation that must be byte-reproducible).
"""

import asyncio
import time


class TokenBucketRateLimiter:
    """Async token-bucket rate limiter."""

    def __init__(self, rate_per_second: float, capacity: int) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be > 0")
        self._rate = rate_per_second
        self._capacity = max(1, int(capacity))
        self._tokens = float(self._capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """Wait until `tokens` are available, then consume them."""
        if tokens <= 0:
            return
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # Sleep until the next token becomes available.
                deficit = tokens - self._tokens
                wait = deficit / self._rate
            await asyncio.sleep(wait)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now
