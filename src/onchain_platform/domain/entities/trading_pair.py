"""TradingPair entity (DOC-012 Part A) — a tradable market.

Slowly-changing registry object (DOC-006 § Structural Domain). Frozen like
every Canonical Schema (DOC-013 § Immutability & State Modeling).
"""

from pydantic import BaseModel, ConfigDict, Field


class TradingPair(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    canonical_id: str  # eip155:<chain_id>/pair:<pool_address>
    chain_id: int = Field(gt=0)
    dex: str  # e.g. "uniswap_v2", "aerodrome"
    base_token_id: str  # Canonical ID of Token
    quote_token_id: str  # Canonical ID of Token
    pool_address: str  # EIP-55 checksummed
    creation_block: int = Field(ge=0)
    creation_fact_id: str  # fact_id of the PAIR_CREATED fact — traceability
