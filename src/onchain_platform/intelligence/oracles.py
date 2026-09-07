"""Deterministic price providers for the multi-source oracle (DOC-012,
DOC-013 Determinism Discipline).

These providers return a fixed/parameterized price with no I/O, so a
historical backfill (or a test) can resolve WETH/ETH USD prices
deterministically. A production deployment would supply a live Chainlink /
on-chain feed instead; the static provider is an explicit, documented stand-in.

Lives in `intelligence/` (may import `domain/` per DOC-011), so it is covered
by `make typecheck` — unlike a function living in `scripts/`.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

# Mirrors the oracle's expected provider signature (acquisition/providers/
# multi_price_oracle.py::EthPriceProvider). Kept local so intelligence/ does
# not need to import acquisition/ (DOC-011).
EthPriceProvider = Callable[[], Awaitable[Decimal]]


class StaticEthPriceProvider:
    """Deterministic ETH/USD price provider for the MultiPriceOracle (WETH).

    Returns a fixed, injected price — no I/O, no wall-clock — for historical
    WETH liquidity_usd backfill and deterministic tests. Satisfies the
    MultiPriceOracle's callable `EthPriceProvider` contract via `__call__`;
    `get_price()` is the explicit method form.

    Used when Chainlink or a real DEX price is unavailable. The price is a
    parameter (never hardcoded logic) so determinism is preserved and callers
    choose the value (DOC-013 Determinism Discipline).
    """

    def __init__(self, price_usd: Decimal) -> None:
        self._price = price_usd

    async def get_price(self) -> Decimal:
        """Return the configured ETH/USD price (explicit method form)."""
        return self._price

    async def __call__(self) -> Decimal:
        """Callable form required by MultiPriceOracle's EthPriceProvider."""
        return self._price
