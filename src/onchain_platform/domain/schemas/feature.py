"""Feature schema (DOC-012 § B.3).

A deterministic analytical transformation. Features are derived from Facts,
Observation Snapshots, Market Bars, Metadata (DOC-008). Features must never
contain future information (Point-in-Time correctness, DOC-008 § D).

Feature.value is `float` — the first genuine float field in the platform
(DOC-012 § Conventions clarification). All other financial fields remain
Decimal/str. The computation itself must still use Decimal inputs
internally; only the final output value's storage type relaxes.

feature_name carries its unit as a suffix (DOC-012 § Feature Naming
Convention): _pct, _ratio, _score, _zscore, _usd, _delta. A feature
without one is missing one — add it before merging.

inputs must be non-empty (DOC-012 § Traceability Chain: "An empty inputs
list is a bug, not an edge case").
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Feature(BaseModel):
    """Deterministic analytical transformation (DOC-012 § B.3).

    Frozen like every Canonical Schema (DOC-013 § Immutability & State
    Modeling).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    # f"{feature_name}|{entity_id}|{as_of_timestamp.isoformat()}" — '|'
    # delimiter, not ':' (DOC-012 § Composite ID Delimiter).
    feature_id: str
    feature_name: str
    entity_id: str
    entity_type: str  # TRADING_PAIR | WALLET | TOKEN
    # The point-in-time this value is valid for. This is the field every
    # PIT-correctness query filters on (DOC-012 § B.3).
    as_of_timestamp: datetime
    # When it was actually computed — may be later than as_of_timestamp
    # for backfilled features, but must never be used for PIT filtering.
    computed_at: datetime
    window: str | None = None  # e.g. "1h", "5m"
    # First genuine float field (DOC-012 § Conventions clarification).
    # All other financial fields are Decimal/str.
    value: float
    # IDs of the Facts / Observation Snapshots / Market Bars this value
    # was derived from — traceability (DOC-012 § Traceability Chain).
    inputs: list[str] = Field(min_length=1)

    @field_validator("feature_name")
    @classmethod
    def _validate_suffix(cls, v: str) -> str:
        """Feature name must end with a required suffix (DOC-012 § Feature
        Naming Convention)."""
        required = ("_pct", "_ratio", "_score", "_zscore", "_usd", "_delta")
        if not any(s in v for s in required):
            raise ValueError(f"feature_name must end with one of {required}, got {v!r}")
        return v

    @field_validator("as_of_timestamp", "computed_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware (UTC), got naive datetime")
        return value

    @field_validator("inputs")
    @classmethod
    def _inputs_non_empty(cls, v: list[str]) -> list[str]:
        """inputs must be non-empty (DOC-012 § Traceability Chain: 'An empty
        inputs list is a bug, not an edge case')."""
        if not v:
            raise ValueError("inputs must be non-empty")
        return v
