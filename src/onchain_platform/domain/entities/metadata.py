"""Metadata entity (DOC-012 Part A) — contextual enrichment.

DOC-006: "Metadata never modifies a Blockchain Fact. This schema has no
event_time — metadata is not a historical occurrence."

Frozen like every Canonical Schema (DOC-013 § Immutability & State Modeling).
Note: Metadata rows are mutable in the DB (verification_status changes over
time) but the Pydantic model is frozen — state change is model_copy(update=...).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from onchain_platform.domain.enums import VerificationStatus


class Metadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    entity_id: str  # Canonical ID of the entity this enriches
    website: str | None = None
    social_links: dict[str, str] = Field(default_factory=dict)
    logo_url: str | None = None
    description: str | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    last_updated: datetime
