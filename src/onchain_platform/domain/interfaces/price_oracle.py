"""Price oracle interface types (TD-1, domain-aware liquidity USD).

PriceSource / PriceResult are pure, dependency-free types shared between the
acquisition oracle implementations (which resolve external/on-chain prices)
and the analytics snapshot job (which consumes USD prices + confidence).

Located in `domain/` so it is importable by both without violating DOC-011's
capability boundaries (domain is below every capability).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from onchain_platform.domain.schemas.enums import QuoteTokenType


@dataclass(frozen=True)
class PoolClassification:
    """Classified shape of a pool relevant to liquidity_usd (a pure value type).

    Houses the quote-token classification that determines which USD price
    formula applies. Shared between the analytics pool classifier (which
    produces it from token addresses) and the acquisition price oracle (which
    consumes it). Located in domain/ so both may import it without violating
    DOC-011. `quote_token_address` is the token whose price we must resolve;
    `is_stablecoin_pool` is True when the quote leg is a stablecoin.
    """

    pool_address: str
    token0: str
    token1: str
    quote_token_type: QuoteTokenType
    quote_token_address: str
    is_stablecoin_pool: bool


class PriceSource(StrEnum):
    """How a USD price was obtained (confidence basis)."""

    STATIC = "STATIC"  # hardcoded stablecoin price (USD=1.0)
    CHAINLINK = "CHAINLINK"  # external ETH/USD feed
    DEX_RATIO = "DEX_RATIO"  # derived from a pool reserve ratio
    NULL = "NULL"  # no price available


@dataclass(frozen=True)
class PriceResult:
    """A USD price with its source and a 0..1 confidence.

    `quote_token_type` tells the caller which leg this price denominates
    (USDC/WETH/STABLECOIN/OTHER), so analytics can pick the correct
    liquidity_usd formula.
    """

    price_usd: Decimal | None
    source: PriceSource
    confidence: float  # 0.0 .. 1.0
    quote_token_type: QuoteTokenType


class PriceOracle(Protocol):
    """USD price source for a token at a point in time."""

    async def get_price(self, token_address: str, as_of: datetime) -> Decimal | None:
        """USD price of the smallest token unit, or None if unknown."""
        ...
