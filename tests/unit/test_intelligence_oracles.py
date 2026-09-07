"""Unit tests: intelligence/oracles.py — StaticEthPriceProvider.

The provider is a deterministic (no-I/O, fixed-price) ETH/USD price source used
by the MultiPriceOracle for WETH liquidity_usd. It is type-checked by mypy
(unlike the old copy that lived in scripts/). Both the callable form (used by
the oracle) and the explicit get_price() form must return the injected price
deterministically.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions). Determinism (DOC-013): same input -> same output, no wall-clock,
no randomness.
"""

from decimal import Decimal

import pytest

from onchain_platform.intelligence.oracles import StaticEthPriceProvider


@pytest.mark.asyncio
async def test_static_eth_price_callable_returns_injected_price() -> None:
    """The callable form (what MultiPriceOracle invokes) returns the injected
    price exactly — deterministic."""
    provider = StaticEthPriceProvider(Decimal("3210.5"))
    assert await provider() == Decimal("3210.5")


@pytest.mark.asyncio
async def test_static_eth_price_get_price_returns_injected_price() -> None:
    """The explicit get_price() form returns the injected price."""
    provider = StaticEthPriceProvider(Decimal("2960"))
    assert await provider.get_price() == Decimal("2960")


@pytest.mark.asyncio
async def test_static_eth_price_is_deterministic() -> None:
    """Same instance, repeated calls -> identical output (DOC-013 determinism)."""
    provider = StaticEthPriceProvider(Decimal("3500"))
    assert await provider() == await provider() == Decimal("3500")
    assert await provider.get_price() == await provider.get_price() == Decimal("3500")
