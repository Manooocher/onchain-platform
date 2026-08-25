"""Multi-provider orchestrator (ADR-006 § Provider Abstraction, Reality Check
Risk: provider downtime / failover).

Tries providers in priority order, skipping unhealthy ones, enforcing each
provider's token-bucket rate limit, and applying bounded exponential backoff
to transient failures. When a provider fails, it is reported to the health
checker (which marks it unhealthy after the configured threshold) and the
orchestrator moves on to the next provider.

Every exception crossing acquisition/ is an AcquisitionError (DOC-013 §
Exception Hierarchy) — a raw httpx/socket error never escapes a provider call.
"""

import asyncio
from collections.abc import Sequence
from typing import Any, cast

import structlog

from onchain_platform.acquisition.providers.base import (
    BlockchainProvider,
    BlockMetadata,
    RawLog,
)
from onchain_platform.acquisition.providers.health_checker import (
    ProviderHealthChecker,
)
from onchain_platform.acquisition.providers.rate_limiter import (
    TokenBucketRateLimiter,
)
from onchain_platform.domain.exceptions import AcquisitionError
from onchain_platform.platform.provider_config import ProviderSpec

logger = structlog.get_logger(__name__)

_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


class AllProvidersFailedError(AcquisitionError):
    """Raised when every provider in the pool could not serve a request."""


class MultiProvider(BlockchainProvider):
    """A BlockchainProvider that fails over across a priority-ordered pool."""

    def __init__(
        self,
        providers: list[tuple[ProviderSpec, BlockchainProvider]],
        health_checker: ProviderHealthChecker,
    ) -> None:
        self._providers = providers
        self._health = health_checker
        self._rate_limiters: dict[str, TokenBucketRateLimiter] = {
            spec.name: TokenBucketRateLimiter(
                spec.rate_limit_per_second,
                max(spec.rate_limit_per_second, 1) * 2,
            )
            for spec, _ in providers
        }

    # ------------------------------------------------------------------
    # BlockchainProvider interface
    # ------------------------------------------------------------------

    async def get_chain_id(self) -> int:
        result = await self._route("get_chain_id")
        return int(result)

    async def get_chain_head(self) -> int:
        result = await self._route("get_chain_head")
        return int(result)

    async def get_block_metadata(self, block_number: int) -> BlockMetadata:
        result = await self._route("get_block_metadata", block_number)
        assert isinstance(result, BlockMetadata)
        return result

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> list[RawLog]:
        result = await self._route("get_logs", from_block, to_block, address, topics)
        return cast("list[RawLog]", result)

    async def close(self) -> None:
        for _, provider in self._providers:
            await provider.close()

    # ------------------------------------------------------------------
    # orchestration
    # ------------------------------------------------------------------

    async def _route(self, method: str, *args: Any) -> Any:
        """Try providers in priority order, skipping unhealthy ones."""
        last_error: Exception | None = None
        attempted: list[str] = []
        for spec, provider in self._providers:
            if not self._health.is_healthy(spec.name):
                if self._health.is_recovery_due(spec.name):
                    logger.info("provider_recovery_due", provider=spec.name)
                    self._health.mark_healthy(spec.name)
                else:
                    continue

            await self._rate_limiters[spec.name].acquire()
            try:
                result = await self._retry_with_backoff(
                    lambda m=method, pr=provider, a=args: _run(pr, m, *a)
                )
                self._health.mark_healthy(spec.name)
                return result
            except AcquisitionError as exc:
                last_error = exc
                attempted.append(spec.name)
                self._health.mark_unhealthy(spec.name)
                logger.warning(
                    "provider_failed",
                    provider=spec.name,
                    method=method,
                    error=str(exc),
                )
                continue

        logger.error("all_providers_failed", method=method, attempted=attempted)
        raise AllProvidersFailedError(
            f"all providers failed for {method} (tried {attempted}): {last_error}"
        )

    async def _retry_with_backoff(self, fn: Any) -> Any:
        """Bounded exponential backoff (1s, 2s, 4s) on AcquisitionError."""
        last_error: Exception | None = None
        for attempt in range(len(_BACKOFF_SECONDS) + 1):
            try:
                return await fn()
            except AcquisitionError as exc:
                last_error = exc
                if attempt < len(_BACKOFF_SECONDS):
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise
        assert last_error is not None
        raise last_error


async def _run(provider: BlockchainProvider, method: str, *args: Any) -> Any:
    """Await an interface method on a provider by name."""
    fn = getattr(provider, method)
    return await fn(*args)
