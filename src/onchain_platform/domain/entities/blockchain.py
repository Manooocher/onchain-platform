"""Blockchain entity (DOC-012 Part A) — a supported EVM-compatible chain.

Slowly-changing registry object (DOC-006 § Structural Domain), stored in
PostgreSQL (DOC-014 § Storage Assignment). Frozen like every Canonical
Schema (DOC-013 § Immutability & State Modeling).
"""

from pydantic import BaseModel, ConfigDict, Field


class Blockchain(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    chain_id: int = Field(gt=0)
    name: str
    native_asset_symbol: str
    is_supported: bool
    # Intentionally float — used to size the reorg header buffer (ADR-006),
    # not a financial value. One of only two sanctioned float fields in the
    # whole schema set (DOC-012 § Clarifying an ambiguity in DOC-008,
    # DOC-014 § Type Mapping Rules "Genuinely float").
    avg_block_time_seconds: float = Field(gt=0)
