"""Unit tests: RiskSignals + Insight schemas (DOC-012 § B.4).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from onchain_platform.domain.schemas.enums import Importance
from onchain_platform.domain.schemas.insight import Insight
from onchain_platform.domain.schemas.risk_signals import RiskSignals

PINNED = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def test_risk_signals_round_trip() -> None:
    rs = RiskSignals(
        is_honeypot="1",
        sell_tax="0.25",
        hidden_owner="1",
        risk_score=0.95,
        risk_indicators=["Honeypot Detected", "Hidden Owner"],
    )
    restored = RiskSignals.model_validate(rs.model_dump())
    assert restored == rs
    assert restored.risk_score == 0.95
    assert isinstance(restored.risk_score, float)


def test_risk_signals_frozen_rejects_mutation() -> None:
    rs = RiskSignals(risk_score=0.5)
    with pytest.raises(ValidationError):
        rs.risk_score = 0.9  # type: ignore[misc]


def test_risk_signals_score_range() -> None:
    with pytest.raises(ValidationError):
        RiskSignals(risk_score=-0.1)
    with pytest.raises(ValidationError):
        RiskSignals(risk_score=1.1)
    # Boundary values accepted.
    RiskSignals(risk_score=0.0)
    RiskSignals(risk_score=1.0)


def test_risk_signals_none_fields_accepted() -> None:
    rs = RiskSignals(risk_score=0.0)
    assert rs.is_honeypot is None
    assert rs.sell_tax is None
    assert rs.risk_indicators == []


def test_insight_round_trip() -> None:
    i = Insight(
        insight_id="insight_001",
        entity_id="eip155:8453/pair:0xabc",
        insight_type="HoneypotDetected",
        summary="This token appears to be a honeypot — cannot sell.",
        generated_at=PINNED,
        source_features=["feat_1"],
        importance=Importance.HIGH,
    )
    restored = Insight.model_validate(i.model_dump())
    assert restored == i
    assert restored.importance == Importance.HIGH


def test_insight_frozen_rejects_mutation() -> None:
    i = Insight(
        insight_id="i1",
        entity_id="e1",
        insight_type="t",
        summary="s",
        generated_at=PINNED,
        importance=Importance.LOW,
    )
    with pytest.raises(ValidationError):
        i.importance = Importance.HIGH  # type: ignore[misc]


def test_insight_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Insight(
            insight_id="i1",
            entity_id="e1",
            insight_type="t",
            summary="s",
            generated_at=datetime(2024, 1, 1),  # naive
            importance=Importance.LOW,
        )


def test_insight_importance_values() -> None:
    for imp in Importance:
        i = Insight(
            insight_id="i1",
            entity_id="e1",
            insight_type="t",
            summary="s",
            generated_at=PINNED,
            importance=imp,
        )
        assert i.importance == imp
