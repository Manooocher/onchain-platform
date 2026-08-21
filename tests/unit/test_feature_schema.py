"""Unit tests: Feature schema (DOC-012 § B.3).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from onchain_platform.domain.schemas.feature import Feature

PINNED = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def _make_feature(
    feature_name: str = "liquidity_growth_pct_1h", inputs: list[str] | None = None
) -> Feature:
    return Feature(
        feature_id=f"{feature_name}|eip155:8453/pair:0xabc|{PINNED.isoformat()}",
        feature_name=feature_name,
        entity_id="eip155:8453/pair:0xabc",
        entity_type="TRADING_PAIR",
        as_of_timestamp=PINNED,
        computed_at=PINNED,
        window="1h",
        value=10.0,
        inputs=inputs or ["snap_1", "snap_2"],
    )


def test_feature_round_trip() -> None:
    f = _make_feature()
    restored = Feature.model_validate(f.model_dump())
    assert restored == f
    assert restored.value == 10.0
    assert isinstance(restored.value, float)


def test_feature_frozen_rejects_mutation() -> None:
    f = _make_feature()
    with pytest.raises(ValidationError):
        f.value = 99.0  # type: ignore[misc]


def test_feature_name_suffix_accepted() -> None:
    # All valid suffixes per DOC-012 § Feature Naming Convention.
    for suffix in ("_pct", "_ratio", "_score", "_zscore", "_usd", "_delta"):
        f = _make_feature(feature_name=f"test_feature{suffix}")
        assert f.feature_name.endswith(suffix)


def test_feature_name_without_suffix_rejected() -> None:
    with pytest.raises(ValidationError, match="must end with one of"):
        _make_feature(feature_name="liquidity_growth_1h")


def test_feature_empty_inputs_rejected() -> None:
    with pytest.raises(ValidationError):
        Feature(
            feature_id="test|entity|2024-01-01",
            feature_name="test_pct",
            entity_id="entity",
            entity_type="TRADING_PAIR",
            as_of_timestamp=PINNED,
            computed_at=PINNED,
            value=1.0,
            inputs=[],
        )


def test_feature_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Feature(
            feature_id="test|entity|2024-01-01",
            feature_name="test_pct",
            entity_id="entity",
            entity_type="TRADING_PAIR",
            as_of_timestamp=datetime(2024, 1, 1),  # naive
            computed_at=PINNED,
            value=1.0,
            inputs=["x"],
        )


def test_feature_id_format() -> None:
    f = _make_feature()
    # Uses '|' delimiter (DOC-012 § Composite ID Delimiter).
    assert "|" in f.feature_id
    parts = f.feature_id.split("|")
    assert len(parts) == 3
    assert parts[0] == "liquidity_growth_pct_1h"
