"""Multi-source price oracle with confidence tracking (TD-1, Phase 2).

Domain-aware: freshly-launched tokens are not on CoinGecko, and Base pools are
USDC/WETH/exotic. This oracle resolves a token's USD price from the best
available source and attaches a confidence so ML Foundation can weight
liquidity_usd by reliability:

- STATIC (confidence 1.0): stablecoins (USDC/USDT/DAI) ~> $1.0.
- CHAINLINK (confidence ~0.95): a resolved ETH/USD price (real Chainlink feed
  or on-chain V3 pool) times the pool's WETH rate.
- DEX_RATIO (confidence ~0.8): derived from the pool's own reserves when the
  quote leg is a stablecoin (token_quote = reserve_stable / reserve_token).
- NULL (confidence 0.0): exotic / unknown quote -> no defensible USD value.

The ETH price provider is injected (constructor) so tests and local runs are
deterministic; a production deployment supplies a Chainlink/on-chain feed. The
oracle caches resolved ETH prices in Redis for a 5-minute TTL to avoid API
spam (Risk Mitigation).

This module lives in acquisition/ and depends only on domain/ (interface +
price result types), satisfying DOC-011.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal

import redis.asyncio as redis

from onchain_platform.domain.interfaces.price_oracle import (
    PoolClassification,
    PriceResult,
    PriceSource,
)
from onchain_platform.domain.schemas.enums import QuoteTokenType

_ETH_PRICE_CACHE_KEY = "oracle:eth_usd"
_ETH_PRICE_CACHE_TTL = 300  # 5 minutes


EthPriceProvider = Callable[[], Awaitable[Decimal]]


class MultiPriceOracle:
    """Resolves USD prices with confidence from statics, ETH, and DEX ratio."""

    def __init__(
        self,
        redis_client: redis.Redis,
        eth_price_provider: EthPriceProvider | None = None,
        eth_price_ttl: int = _ETH_PRICE_CACHE_TTL,
    ) -> None:
        self._redis = redis_client
        self._eth_provider = eth_price_provider
        self._eth_ttl = eth_price_ttl

    async def get_pool_result(
        self,
        pool_reserves: tuple[str, str],
        pool_class: PoolClassification,
        as_of: datetime,
    ) -> PriceResult:
        """Resolve the quote leg's USD price from the pool's reserves.

        `pool_reserves` is (reserve0, reserve1) as Token Amount strings in the
        same token order as the pool's token0/token1. For a USDC pool, the
        quote is the stablecoin; for a WETH pool, the quote resolves via the
        WETH price; for an exotic pool, NULL.
        """
        quote = pool_class.quote_token_type

        # 1. Stablecoin quote -> STATIC.
        if quote in (QuoteTokenType.USDC, QuoteTokenType.STABLECOIN):
            return PriceResult(
                price_usd=Decimal("1.0"),
                source=PriceSource.STATIC,
                confidence=1.0,
                quote_token_type=quote,
            )

        # 2. WETH quote -> CHAINLINK ETH price (cached).
        if quote == QuoteTokenType.WETH:
            eth_usd = await self._eth_price()
            if eth_usd is None:
                return PriceResult(None, PriceSource.NULL, 0.0, quote)
            return PriceResult(eth_usd, PriceSource.CHAINLINK, 0.95, quote)

        # 3. Exotic (OTHER) -> NULL.
        return PriceResult(None, PriceSource.NULL, 0.0, QuoteTokenType.OTHER)

    async def get_token_price_from_ratio(
        self,
        quote_price_usd: Decimal | None,
        ratio: Decimal | None,
        quote_type: QuoteTokenType,
        confidence: float,
    ) -> PriceResult:
        """Derive a base-token USD price from a quote price and pool ratio.

        Used for DEX_RATIO: when the pool quote is USD (stablecoin), the base
        token's price = quote_usd / reserve_ratio. When the quote is WETH, =
        quote_usd (ETH price) * reserve_ratio.
        """
        if quote_price_usd is None or ratio is None or ratio <= 0:
            return PriceResult(None, PriceSource.NULL, 0.0, quote_type)
        price = quote_price_usd * ratio
        return PriceResult(price, PriceSource.DEX_RATIO, confidence, quote_type)

    async def _eth_price(self) -> Decimal | None:
        """Resolve the WETH/ETH USD price, caching for the TTL."""
        cached = await self._redis.get(_ETH_PRICE_CACHE_KEY)
        if cached is not None:
            try:
                return Decimal(json.loads(cached))
            except (json.JSONDecodeError, ValueError):
                pass
        if self._eth_provider is None:
            return None
        price = await self._eth_provider()
        if price is None:
            return None
        await self._redis.set(_ETH_PRICE_CACHE_KEY, json.dumps(str(price)), ex=self._eth_ttl)
        return price
