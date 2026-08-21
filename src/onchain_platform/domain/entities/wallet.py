"""Wallet entity (DOC-012 Part A) — a blockchain account.

Slowly-changing registry object (DOC-006 § Structural Domain). Frozen like
every Canonical Schema (DOC-013 § Immutability & State Modeling).

DOC-012: tags is "Empty in MVP. Placeholder for DOC-006 Future Extensions
(developer, smart_money, etc.) — populated by later phases, not MVP logic."
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Wallet(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    canonical_id: str  # eip155:<chain_id>/wallet:<address>
    chain_id: int = Field(gt=0)
    address: str  # EIP-55 checksummed
    first_seen_at: datetime  # event_time of the first Fact referencing this wallet
    tags: list[str] = Field(default_factory=list)  # Empty in MVP
