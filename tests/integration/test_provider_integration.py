"""Integration tests: provider connectivity against real Base chain (Phase E).

Marked `live` (network-dependent; never gates CI). Verifies:
- A MultiProvider built from the configured Base pool returns a real chain head
  (routed to whichever provider serves it — Alchemy/QuickNode are reachable).
- Failover: an injected failing primary is routed around, and the pool still
  returns a valid head.

These require provider API keys set in the environment (see .env.example).
"""

import os

import pytest

from onchain_platform.acquisition.providers.base import BlockchainProvider
from onchain_platform.acquisition.providers.factory import create_provider
from onchain_platform.acquisition.providers.health_checker import (
    ProviderHealthChecker,
)
from onchain_platform.acquisition.providers.multi_provider import MultiProvider
from onchain_platform.domain.exceptions import AcquisitionError
from onchain_platform.platform.provider_config import (
    ProviderSpec,
    load_provider_config,
)

_REQUIRED_ENV = [
    "ALCHEMY_BASE_API_KEY",
    "QUICKNODE_BASE_API_KEY",
    "QUICKNODE_BASE_SUBDOMAIN",
]

pytestmark = pytest.mark.live


def _keys_present() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED_ENV)


class _FailingPrimary(BlockchainProvider):
    async def get_chain_id(self) -> int:  # pragma: no cover - never used
        raise AcquisitionError("primary down")

    async def get_chain_head(self) -> int:
        raise AcquisitionError("primary down")

    async def get_block_metadata(self, block_number: int):  # pragma: no cover
        raise AcquisitionError("primary down")

    async def get_logs(self, from_block, to_block, address=None, topics=None):  # pragma: no cover
        raise AcquisitionError("primary down")

    async def close(self) -> None:
        return None


async def test_multi_provider_fetches_live_head() -> None:
    """The configured Base pool returns a real head block (via Alchemy primary)."""
    if not _keys_present():
        pytest.skip("provider keys not set in environment")
    cfg = load_provider_config("base")
    providers = [(spec, create_provider(spec)) for spec in cfg.providers]
    multi = MultiProvider(providers, ProviderHealthChecker(cfg))
    try:
        head = await multi.get_chain_head()
        assert head > 0, "expected a positive live head block"
    finally:
        await multi.close()


async def test_multi_provider_routes_around_failing_primary() -> None:
    """A failing injected primary is either marked-unhealthy or skipped, and
    the pool's healthy real provider still serves a head block."""
    if not _keys_present():
        pytest.skip("provider keys not set in environment")
    cfg = load_provider_config("base")
    primary_spec = ProviderSpec(
        name="failing_primary",
        type="local_node",
        url="https://failing.invalid",
        ws_url=None,
        rate_limit_per_second=10,
        priority=0,  # highest priority -> tried first, fails
    )
    real = [(spec, create_provider(spec)) for spec in cfg.providers]
    providers = [(primary_spec, _FailingPrimary()), *real]
    multi = MultiProvider(providers, ProviderHealthChecker(cfg))
    try:
        head = await multi.get_chain_head()
        assert head > 0, "failover should serve a real head from a healthy provider"
    finally:
        await multi.close()
