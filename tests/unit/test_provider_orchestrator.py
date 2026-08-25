"""Unit tests: provider orchestrator pieces (Phase C).

Covers the token-bucket rate limiter, the health checker's unhealthy/recovery
logic, and MultiProvider failover via fake providers (no network).
"""

from collections.abc import Sequence

import pytest

from onchain_platform.acquisition.providers.base import (
    BlockchainProvider,
    BlockMetadata,
    RawLog,
)
from onchain_platform.acquisition.providers.health_checker import (
    ProviderHealthChecker,
)
from onchain_platform.acquisition.providers.multi_provider import (
    AllProvidersFailedError,
    MultiProvider,
)
from onchain_platform.acquisition.providers.rate_limiter import (
    TokenBucketRateLimiter,
)
from onchain_platform.domain.exceptions import AcquisitionError
from onchain_platform.platform.provider_config import ChainProviderConfig, ProviderSpec


def _spec(name: str, priority: int = 1, rate: int = 10) -> ProviderSpec:
    return ProviderSpec(
        name=name,
        type="local_node",
        url="https://x",
        ws_url=None,
        rate_limit_per_second=rate,
        priority=priority,
    )


def _config(specs: list[ProviderSpec]) -> ChainProviderConfig:
    return ChainProviderConfig(
        chain_id=8453,
        providers=specs,
        strategy="priority_with_health_check",
        health_check_interval_seconds=30,
        unhealthy_threshold=3,
        recovery_interval_seconds=300,
    )


# --- Rate limiter ---


def test_rate_limiter_capacity() -> None:
    limiter = TokenBucketRateLimiter(rate_per_second=1000, capacity=100)
    # First capacity acquisitions are instant (tokens preloaded).
    assert limiter._tokens == 100  # type: ignore[attr-defined]


# --- Health checker ---


def test_health_checker_marks_unhealthy_after_threshold() -> None:
    specs = [_spec("a", priority=1)]
    checker = ProviderHealthChecker(_config(specs))
    assert checker.is_healthy("a") is True
    for _ in range(2):
        checker.mark_unhealthy("a")  # 0,1
    assert checker.is_healthy("a") is True  # below threshold (2 < 3)
    checker.mark_unhealthy("a")  # reaches 3 -> unhealthy
    assert checker.is_healthy("a") is False


def test_health_checker_marks_healthy_resets() -> None:
    checker = ProviderHealthChecker(_config([_spec("a")]))
    for _ in range(3):
        checker.mark_unhealthy("a")
    assert checker.is_healthy("a") is False
    checker.mark_healthy("a")
    assert checker.is_healthy("a") is True
    assert checker._consecutive_failures["a"] == 0  # type: ignore[attr-defined]


def test_health_checker_recovery_due_when_never_unhealthy() -> None:
    checker = ProviderHealthChecker(_config([_spec("a")]))
    assert checker.is_recovery_due("a") is True  # never marked unhealthy


# --- Fake providers for failover ---


class _FakeProvider(BlockchainProvider):
    def __init__(self, name: str, fail: bool = False, head: int = 1) -> None:
        self.name = name
        self.fail = fail
        self.head = head
        self.calls = 0

    async def get_chain_id(self) -> int:
        raise NotImplementedError

    async def get_chain_head(self) -> int:
        self.calls += 1
        if self.fail:
            raise AcquisitionError(f"{self.name} down")
        return self.head

    async def get_block_metadata(self, block_number: int) -> BlockMetadata:
        raise NotImplementedError

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> list[RawLog]:
        raise NotImplementedError

    async def close(self) -> None:  # pragmatic noop for tests
        return None


def test_multi_provider_fails_over_to_healthy_secondary() -> None:
    primary = _FakeProvider("primary", fail=True, head=10)
    secondary = _FakeProvider("secondary", fail=False, head=20)
    specs = [_spec("primary", priority=1), _spec("secondary", priority=2)]
    checker = ProviderHealthChecker(_config(specs))
    multi = MultiProvider(list(zip(specs, [primary, secondary], strict=True)), checker)

    import asyncio

    assert asyncio.run(multi.get_chain_head()) == 20
    assert secondary.calls == 1


def test_multi_provider_fails_when_all_down() -> None:
    primary = _FakeProvider("a", fail=True)
    secondary = _FakeProvider("a", fail=True)
    specs = [_spec("p1", priority=1), _spec("p2", priority=2)]
    checker = ProviderHealthChecker(_config(specs))
    multi = MultiProvider(list(zip(specs, [primary, secondary], strict=True)), checker)

    import asyncio

    with pytest.raises(AllProvidersFailedError):
        asyncio.run(multi.get_chain_head())
