"""Insight schema (DOC-012 § B.4).

DOC-008: "Insights summarize Features." An Insight never becomes input to
a downstream pipeline — no other schema may reference an insight_id in an
inputs field.

importance is a qualitative editorial signal, explicitly NOT an ML
confidence score (DOC-012 § B.4).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from onchain_platform.domain.schemas.enums import Importance


class Insight(BaseModel):
    """Human-readable research conclusion (DOC-012 § B.4).

    Frozen per DOC-013 § Immutability & State Modeling.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    insight_id: str
    entity_id: str  # Canonical ID of the entity this enriches
    insight_type: str  # e.g. "HoneypotDetected", "HighSellTax"
    summary: str  # Human-readable, one to two sentences
    generated_at: datetime
    source_features: list[str] = Field(default_factory=list)
    importance: Importance

    @field_validator("generated_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware (UTC)")
        return value
