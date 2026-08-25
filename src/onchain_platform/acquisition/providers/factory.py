"""Provider factory (ADR-006 § Provider Abstraction).

Maps a resolved ProviderSpec to a concrete BlockchainProvider. The factory is
the only place a provider class name appears as a string; everything else
depends only on the interface.
"""

from onchain_platform.acquisition.providers.alchemy import AlchemyProvider
from onchain_platform.acquisition.providers.base import BlockchainProvider
from onchain_platform.acquisition.providers.local_node import LocalNodeProvider
from onchain_platform.acquisition.providers.quiknode import QuickNodeProvider
from onchain_platform.acquisition.providers.rockx import RockXProvider
from onchain_platform.domain.exceptions import AcquisitionError
from onchain_platform.platform.provider_config import ProviderSpec


def create_provider(spec: ProviderSpec) -> BlockchainProvider:
    """Create a concrete provider from a resolved spec.

    Raises AcquisitionError for an unknown provider type (a config or
    mis-wiring bug, not a runtime concern).
    """
    url = spec.url
    ws_url = spec.ws_url

    if spec.type == "alchemy":
        return AlchemyProvider(url, ws_url=ws_url)
    if spec.type == "quiknode":
        return QuickNodeProvider(url)
    if spec.type == "rockx_w3node":
        return RockXProvider(url)
    if spec.type == "local_node":
        return LocalNodeProvider(url)
    # Defensive fallback so a mis-typed config never reaches the collector.
    raise AcquisitionError(f"unknown provider type: {spec.type!r}")
