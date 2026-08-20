"""Checkpoint schema (DOC-012 § B.0).

Tracks ingestion progress per chain, so a restart knows where to resume
(ADR-006 § Checkpointing). Read by acquisition/checkpoint.py; written/
advanced only by processing/finality_engine.py (DOC-011) — nothing is
finalized, so nothing should advance this, outside the finality engine.

Mutable, singleton per chain, not append-only. This is the direct opposite
of BlockchainFact (§ B.1): there is exactly one Checkpoint row per chain_id,
and it is overwritten in place as ingestion progresses (DOC-012 § B.0).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Checkpoint(BaseModel):
    """Ingestion checkpoint per chain (DOC-012 § B.0)."""

    model_config = ConfigDict(frozen=True)

    chain_id: int = Field(gt=0)
    last_finalized_block: int = Field(ge=0)
    last_finalized_at: datetime
    updated_at: datetime
