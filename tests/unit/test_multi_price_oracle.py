"""Unit tests: multi-source price oracle with confidence (TD-1, Phase 2)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import redis.asyncio as redis

from onchain_platform.acquisition.providers.multi_price_oracle import (
    MultiPriceOracle,
)
from onchain_platform.domain.interfaces.price_oracle import (
    PoolClassification,
    PriceSource,
)
from onchain_platform.domain.schemas.enums import QuoteTokenType

PINNED = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
POOL = "0x9999"
TOKEN0 = "0x1111"
TOKEN1 = "0x2222"


@pytest.fixture
def redis_client() -> redis.Redis:
    return redis.from_url("redis://localhost:6379/0")


def _cls(quote: QuoteTokenType) -> PoolClassification:
    return PoolClassification(
        pool_address=POOL,
        token0=TOKEN0,
        token1=TOKEN1,
        quote_token_type=quote,
        quote_token_address=TOKEN1,
        is_stablecoin_pool=quote in (QuoteTokenType.USDC, QuoteTokenType.STABLECOIN),
    )


async def test_stablecoin_quote_is_static_confidence_1(redis_client: redis.Redis) -> None:
    await redis_client.flushdb()
    o = MultiPriceOracle(redis_client)
    res = await o.get_pool_result(("1000", "2000"), _cls(QuoteTokenType.USDC), PINNED)
    assert res.price_usd == Decimal("1.0")
    assert res.source == PriceSource.STATIC
    assert res.confidence == 1.0
    assert res.quote_token_type == QuoteTokenType.USDC


async def test_weth_quote_uses_eth_price_chainlink_confidence(
    redis_client: redis.Redis,
) -> None:
    await redis_client.flushdb()

    async def eth() -> Decimal:
        return Decimal("3000")

    o = MultiPriceOracle(redis_client, eth_price_provider=eth)
    res = await o.get_pool_result(("1", "1"), _cls(QuoteTokenType.WETH), PINNED)
    assert res.price_usd == Decimal("3000")
    assert res.source == PriceSource.CHAINLINK
    assert res.confidence == 0.95


async def test_weth_no_eth_provider_returns_null(redis_client: redis.Redis) -> None:
    await redis_client.flushdb()
    o = MultiPriceOracle(redis_client, eth_price_provider=None)
    res = await o.get_pool_result(("1", "1"), _cls(QuoteTokenType.WETH), PINNED)
    assert res.price_usd is None
    assert res.source == PriceSource.NULL
    assert res.confidence == 0.0


async def test_exotic_pool_returns_null_confidence_0(redis_client: redis.Redis) -> None:
    await redis_client.flushdb()
    o = MultiPriceOracle(redis_client)
    res = await o.get_pool_result(("1", "1"), _cls(QuoteTokenType.OTHER), PINNED)
    assert res.price_usd is None
    assert res.source == PriceSource.NULL
    assert res.confidence == 0.0


async def test_dex_ratio_derives_base_price(redis_client: redis.Redis) -> None:
    await redis_client.flushdb()
    o = MultiPriceOracle(redis_client)
    # quote_usd=1.0 (USDC), ratio=0.5 → base price 0.5.
    res = await o.get_token_price_from_ratio(
        Decimal("1.0"), Decimal("0.5"), QuoteTokenType.USDC, 0.8
    )
    assert res.price_usd == Decimal("0.5")
    assert res.source == PriceSource.DEX_RATIO
    assert res.confidence == 0.8


async def test_dex_ratio_bad_ratio_returns_null(redis_client: redis.Redis) -> None:
    await redis_client.flushdb()
    o = MultiPriceOracle(redis_client)
    res = await o.get_token_price_from_ratio(Decimal("1.0"), None, QuoteTokenType.USDC, 0.8)
    assert res.price_usd is None
    assert res.source == PriceSource.NULL


async def test_eth_price_cached(redis_client: redis.Redis) -> None:
    await redis_client.flushdb()
    calls = 0

    async def eth() -> Decimal:
        nonlocal calls
        calls += 1
        return Decimal("3000")

    o = MultiPriceOracle(redis_client, eth_price_provider=eth)
    await o._eth_price()
    await o._eth_price()
    assert calls == 1  # cached after first resolve
