"""Price oracle Protocol (TD-1, Phase 5).

A price source for a token address + a point-in-time to a USD price of the
raw (smallest-denomination) token unit. It is the bridge between the on-chain
reserves (token amounts) and USD-liquidity that ML Foundation (Phase 4)
needs.

Located in `domain/` so it is a pure, dependency-free interface that both the
acquisition oracle implementations and the analytics projection engine can
import without violating DOC-011's capability boundaries.
"""

from datetime import datetime
from decimal import Decimal
from typing import Protocol


class PriceOracle(Protocol):
    """USD price source for a token at a point in time."""

    async def get_price(self, token_address: str, as_of: datetime) -> Decimal | None:
        """USD price of the smallest token unit, or None if unknown."""
        ...
