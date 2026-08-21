"""LiquidityPool entity (DOC-012 Part A) — liquidity backing a TradingPair.

Slowly-changing registry object (DOC-006 § Structural Domain). Frozen like
every Canonical Schema (DOC-013 § Immutability & State Modeling).

DOC-012: "a Liquidity Pool does not have an identity independent of its
pair in the MVP" — canonical_id is the same as TradingPair.canonical_id.
Current reserves are NOT stored here — that is live State Projection
(DOC-012 § Part B). This schema only holds the pool's static configuration.
"""

from pydantic import BaseModel, ConfigDict


class LiquidityPool(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    canonical_id: str  # Same as parent TradingPair.canonical_id
    protocol: str
    fee_tier_bps: int | None = None  # None for fee-less V2-style pools
