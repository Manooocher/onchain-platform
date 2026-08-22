"""Unit tests: Outcome schema (DOC-012 § B.4).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from onchain_platform.domain.schemas.enums import OutcomeType
from onchain_platform.domain.schemas.outcome import Outcome

PINNED = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
ENTITY_ID = "eip155:8453/pair:0x39f0E675D479088DE08b7f201Ac08e20F899B838"


def _make_outcome(
    *,
    outcome_id: str = f"{ENTITY_ID}|RUG_PULL|{PINNED.isoformat()}",
    entity_id: str = ENTITY_ID,
    outcome_type: OutcomeType = OutcomeType.RUG_PULL,
    observation_window: str = "1h",
    label_definition: str = "Liquidity drops >90% OR honeypot detected",
    label_definition_version: str = "1.0",
    evaluation_timestamp: datetime = PINNED,
    evaluated_at: datetime = PINNED,
    label_value: bool = True,
) -> Outcome:
    return Outcome(
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


def test_outcome_round_trip() -> None:
    o = _make_outcome()
    restored = Outcome.model_validate(o.model_dump())
    assert restored == o
    assert restored.label_value is True
    assert restored.outcome_type == OutcomeType.RUG_PULL
    assert restored.label_definition_version == "1.0"


def test_outcome_id_format_accepted_with_pipe() -> None:
    # '|' delimiter is the valid form (DOC-012 § Composite ID Delimiter).
    o = _make_outcome()
    parts = o.outcome_id.split("|")
    assert len(parts) == 3
    assert parts[0] == ENTITY_ID
    assert parts[1] == "RUG_PULL"
    assert parts[2] == PINNED.isoformat()


def test_outcome_id_zero_or_two_parts_rejected() -> None:
    # A ':'-delimited or malformed outcome_id must be rejected — ':'-splitting
    # is ambiguous because the Canonical ID and ISO timestamp both contain ':'
    # (DOC-012 § Composite ID Delimiter).
    with pytest.raises(ValidationError, match="three"):
        _make_outcome(outcome_id=f"{ENTITY_ID}:RUG_PULL:{PINNED.isoformat()}")
    with pytest.raises(ValidationError, match="three"):
        _make_outcome(outcome_id=f"{ENTITY_ID}|RUG_PULL")  # only 2 parts


def test_outcome_create_derives_id() -> None:
    o = Outcome.create(
        entity_id=ENTITY_ID,
        outcome_type=OutcomeType.SUCCESSFUL_LAUNCH,
        observation_window="24h",
        label_definition="launch rules",
        label_definition_version="1.0",
        evaluation_timestamp=PINNED,
        evaluated_at=PINNED,
        label_value=False,
    )
    assert o.outcome_id == f"{ENTITY_ID}|SUCCESSFUL_LAUNCH|{PINNED.isoformat()}"
    assert o.outcome_type == OutcomeType.SUCCESSFUL_LAUNCH


def test_outcome_frozen_rejects_mutation() -> None:
    o = _make_outcome()
    with pytest.raises(ValidationError):
        o.label_value = False  # type: ignore[misc]


def test_outcome_naive_timestamps_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _make_outcome(evaluation_timestamp=datetime(2026, 8, 22, 12, 0, 0))  # naive
    with pytest.raises(ValidationError, match="timezone-aware"):
        _make_outcome(evaluated_at=datetime(2026, 8, 22, 12, 0, 0))  # naive


def test_outcome_all_three_types_valid() -> None:
    # Document the three labels are all valid constructs (DOC-012 § B.4).
    for t in (OutcomeType.RUG_PULL, OutcomeType.SUCCESSFUL_LAUNCH, OutcomeType.DEAD_TOKEN):
        o = Outcome.create(
            entity_id=ENTITY_ID,
            outcome_type=t,
            observation_window="1h",
            label_definition="rules",
            label_definition_version="1.0",
            evaluation_timestamp=PINNED,
            evaluated_at=PINNED,
            label_value=False,
        )
        assert o.outcome_type == t
