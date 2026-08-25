"""Unit tests: provider implementations + factory (Phase B).

The providers share one HTTP JSON-RPC base; here we verify class wiring,
factory dispatch, TLS posture, and the head-parsing path without hitting the
network (the base's `_call` is stubbed for the one parse test).
"""

from typing import Any

import pytest

from onchain_platform.acquisition.providers.alchemy import AlchemyProvider
from onchain_platform.acquisition.providers.base import BlockchainProvider
from onchain_platform.acquisition.providers.factory import create_provider
from onchain_platform.acquisition.providers.http_json_rpc import HttpJsonRpcProvider
from onchain_platform.acquisition.providers.quiknode import QuickNodeProvider
from onchain_platform.acquisition.providers.rockx import RockXProvider
from onchain_platform.domain.exceptions import AcquisitionError
from onchain_platform.platform.provider_config import ProviderSpec


def test_alchemy_is_http_json_rpc_provider() -> None:
    """Alchemy subclasses the shared base and carries a ws_url."""
    p = AlchemyProvider("https://x/v2/key", ws_url="wss://x/v2/key")
    assert isinstance(p, HttpJsonRpcProvider)
    assert isinstance(p, BlockchainProvider)
    assert p._ws_url == "wss://x/v2/key"  # type: ignore[attr-defined]


def test_quiknode_subclasses_base_and_drops_ws() -> None:
    p = QuickNodeProvider("https://q.example/")
    assert isinstance(p, HttpJsonRpcProvider)
    assert p._ws_url is None  # type: ignore[attr-defined]


def test_rockx_disables_tls_verification() -> None:
    p = RockXProvider("https://base.w3node.com/key/api")
    assert isinstance(p, BlockchainProvider)
    assert p._verify_tls is False  # type: ignore[attr-defined]


def test_factory_maps_types() -> None:
    def _spec(ptype: str) -> ProviderSpec:
        return ProviderSpec(
            name=ptype,
            type=ptype,
            url="https://x/endpoint",
            ws_url=None,
            rate_limit_per_second=10,
            priority=1,
        )

    assert isinstance(create_provider(_spec("alchemy")), AlchemyProvider)
    assert isinstance(create_provider(_spec("quiknode")), QuickNodeProvider)
    assert isinstance(create_provider(_spec("rockx_w3node")), RockXProvider)


def test_factory_unknown_type_raises() -> None:
    spec = ProviderSpec(
        name="bogus",
        type="bogus",
        url="https://x",
        ws_url=None,
        rate_limit_per_second=1,
        priority=1,
    )
    with pytest.raises(AcquisitionError, match="unknown provider type"):
        create_provider(spec)


async def _head_with_stub(provider: HttpJsonRpcProvider) -> int:
    """Call get_chain_head with provider._call stubbed to a known hex."""

    async def _ours(method: str, params: list[Any]) -> str:
        assert method == "eth_blockNumber"
        return hex(50_402_845)

    original = provider._call  # type: ignore[attr-defined]
    provider._call = _ours  # type: ignore[attr-defined]
    try:
        return await provider.get_chain_head()
    finally:
        provider._call = original  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_provider_parses_chain_head_hex() -> None:
    """get_chain_head parses eth_blockNumber: '0x...' -> int without network."""
    p = AlchemyProvider("https://x/v2/key")
    assert await _head_with_stub(p) == 50_402_845
