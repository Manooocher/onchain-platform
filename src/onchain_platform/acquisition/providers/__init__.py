"""Provider package exports.

`create_multi_provider(chain)` builds the full provider pool + health checker
+ rate limiters for a chain from config, returning a BlockchainProvider that
fails over across the pool. This is the composition entry point used by
main.py's `--chain` flag.
"""

from onchain_platform.acquisition.providers.factory import create_provider
from onchain_platform.acquisition.providers.health_checker import (
    ProviderHealthChecker,
)
from onchain_platform.acquisition.providers.multi_provider import MultiProvider
from onchain_platform.platform.provider_config import load_provider_config


def create_multi_provider(chain: str) -> MultiProvider:
    """Build a failover BlockchainProvider pool for a chain.

    Loads config/providers.yaml for `chain`, resolves env placeholders, builds
    one concrete provider per spec (priority order), and wraps them in a
    MultiProvider with a health checker.
    """
    config = load_provider_config(chain)
    providers = [(spec, create_provider(spec)) for spec in config.providers]
    health_checker = ProviderHealthChecker(config)
    return MultiProvider(providers, health_checker)


__all__ = [
    "create_multi_provider",
    "create_provider",
    "MultiProvider",
    "ProviderHealthChecker",
]
