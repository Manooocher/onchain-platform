"""Static price oracle implementation (TD-1, Phase 5).

A deterministic, no-network implementation of the domain PriceOracle. It
returns a fixed USD-per-token price map and is used as the local/fallback
oracle and in integration tests.

A real CoinGecko / on-chain TWAP oracle would implement the same domain
Protocol and live in this package (acquisition resolves external prices);
wiring one that requires a live API key + network is a deployment step, not
part of the deterministic core.
"""

from datetime import datetime
from decimal import Decimal

from onchain_platform.domain.interfaces.price_oracle import PriceOracle


class StaticPriceOracle(PriceOracle):
    """Returns a fixed USD price per token address (no network)."""

    def __init__(self, prices: dict[str, str | Decimal]) -> None:
        """prices maps a lowercase token address → USD price of one raw unit."""
        self._prices = {addr.lower(): Decimal(p) for addr, p in prices.items()}

    async def get_price(self, token_address: str, as_of: datetime) -> Decimal | None:
        """Return the configured USD price for the token, or None if unknown."""
        _ = as_of  # static oracle ignores time
        return self._prices.get(token_address.lower())
