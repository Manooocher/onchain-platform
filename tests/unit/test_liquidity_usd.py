"""Unit tests: liquidity_usd computation + price oracle (TD-1, Phase 5)."""

from decimal import Decimal

from onchain_platform.acquisition.providers.static_price_oracle import (
    StaticPriceOracle,
)
from onchain_platform.analytics.snapshot_job import compute_liquidity_usd


def test_compute_liquidity_usd_decimal_math() -> None:
    # reserve0=1000, price0=2.5  => 2500; reserve1=2000, price1=1.0 => 2000.
    # total = 4500. Exact Decimal, no float.
    result = compute_liquidity_usd("1000", "2000", Decimal("2.5"), Decimal("1.0"))
    assert result is not None
    assert Decimal(result) == Decimal("4500")


def test_compute_liquidity_usd_returns_none_when_price_unknown() -> None:
    assert compute_liquidity_usd("1000", "2000", None, Decimal("1.0")) is None
    assert compute_liquidity_usd("1000", "2000", Decimal("1.0"), None) is None


def test_compute_liquidity_usd_malformed_reserve_returns_none() -> None:
    assert compute_liquidity_usd("not-a-number", "2000", Decimal("1"), Decimal("1")) is None


def test_static_price_oracle_returns_configured_prices() -> None:
    import asyncio
    from datetime import UTC, datetime

    oracle = StaticPriceOracle({"0xabc": "2.5", "0xdef": "1.0"})
    price0 = asyncio.run(oracle.get_price("0xabc", datetime.now(UTC)))
    price1 = asyncio.run(oracle.get_price("0xDEF", datetime.now(UTC)))
    assert price0 == Decimal("2.5")
    assert price1 == Decimal("1.0")


def test_static_price_oracle_unknown_token_is_none() -> None:
    import asyncio
    from datetime import UTC, datetime

    oracle = StaticPriceOracle({"0xaa": "2.5"})
    p = asyncio.run(oracle.get_price("0xzz", datetime.now(UTC)))
    assert p is None
