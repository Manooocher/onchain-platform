"""Outcome schema (DOC-012 § B.4, DOC-008 § Outcome).

A ground-truth label assigned to a pair after its observation window closes.
Outcomes are the training labels for the ML Foundation (DOC-005 Phase 4), so
the label_definition must be versioned and reproducible.

DOC-008: "Outcomes never contain confidence. Confidence belongs to
Predictions." There is deliberately NO confidence field on this schema.

Frozen per DOC-013 § Immutability & State Modeling — state change is always
model_copy(update=...), never mutation.

outcome_id uses '|' as the outer delimiter (DOC-012 § Composite ID
Delimiter): the embedded Canonical ID (has ':' and '/') and ISO timestamp
(has ':') would make ':'-splitting ambiguous.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from onchain_platform.domain.schemas.enums import OutcomeType


class Outcome(BaseModel):
    """A ground-truth label for a pair whose observation window has closed
    (DOC-012 § B.4)."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    # f"{entity_id}|{outcome_type}|{evaluation_timestamp.isoformat()}'
    # — '|' delimiter, not ':' (DOC-012 § Composite ID Delimiter).
    outcome_id: str
    entity_id: str  # Canonical ID of the TradingPair
    outcome_type: OutcomeType  # RUG_PULL | SUCCESSFUL_LAUNCH | DEAD_TOKEN
    observation_window: str  # e.g. "1h", "24h"
    label_definition: str  # Human-readable rule description
    label_definition_version: str  # e.g. "1.0" — rules evolve independently of schema_version
    # When the observation window closed. Deterministic (creation_time +
    # window), NOT evaluated_at — DOC-012 § B.4: this is what the outcome_id
    # is keyed on, so it must be reproducible across re-runs.
    evaluation_timestamp: datetime
    # When the Outcome Engine actually ran this evaluation.
    evaluated_at: datetime
    # Did this outcome occur, per this rule version?
    label_value: bool

    @field_validator("outcome_id")
    @classmethod
    def _outcome_id_format(cls, v: str) -> str:
        # DOC-012 § Composite ID Delimiter: '|', never ':'. Splitting on '|'
        # must yield exactly three parts.
        parts = v.split("|")
        if len(parts) != 3:
            raise ValueError(
                f"outcome_id must have exactly three '|'-separated components "
                f"(entity_id|outcome_type|evaluation_timestamp), got {v!r}"
            )
        return v

    @field_validator("evaluation_timestamp", "evaluated_at")
    @classmethod
    def _timezone_aware(cls, v: datetime) -> datetime:
        # All timestamps are timezone-aware UTC (DOC-012 § Conventions).
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware (UTC), got naive datetime")
        return v

    @classmethod
    def create(
        cls,
        *,
        entity_id: str,
        outcome_type: OutcomeType,
        observation_window: str,
        label_definition: str,
        label_definition_version: str,
        evaluation_timestamp: datetime,
        evaluated_at: datetime,
        label_value: bool,
    ) -> "Outcome":
        """Factory that derives outcome_id from its components
        (DOC-012 § B.4)."""
        outcome_id = f"{entity_id}|{outcome_type.value}|{evaluation_timestamp.isoformat()}"
        return cls(
            outcome_id=outcome_id,
            entity_id=entity_id,
            outcome_type=outcome_type,
            observation_window=observation_window,
            label_definition=label_definition,
            label_definition_version=label_definition_version,
            evaluation_timestamp=evaluation_timestamp,
            evaluated_at=evaluated_at,
            label_value=label_value,
        )
