"""Unit tests: ranking engine determinism + normalization (Phase B).

The normalization sub-scores and factor-building are pure and deterministic.
Determinism of `compute_ranking` is proven against a seeded real Postgres in
the integration suite; here we test the pure pieces plus that the config and
factor ordering are stable.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013).
"""

from datetime import UTC, datetime

import pytest

from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.strategy import ranking_config as cfg
from onchain_platform.strategy.ranking import (
    _candidate_factors,
    _normalize_liquidity_growth,
    _normalize_momentum_zscore,
)

_PINNED = datetime(2026, 1, 1, tzinfo=UTC)


def _feature(name: str, value: float) -> Feature:
    return Feature(
        feature_id=f"{name}|entity|{_PINNED.isoformat()}",
        feature_name=name,
        entity_id="entity",
        entity_type="TRADING_PAIR",
        as_of_timestamp=_PINNED,
        computed_at=_PINNED,
        value=value,
        inputs=["s"],
    )


def test_normalize_liquidity_growth_zero_to_one() -> None:
    assert _normalize_liquidity_growth(0.0) == 0.0
    assert _normalize_liquidity_growth(0.5) == 0.5
    assert _normalize_liquidity_growth(1.0) == 1.0  # at cap
    assert _normalize_liquidity_growth(5.0) == 1.0  # above cap saturates


def test_normalize_momentum_zscore_signed() -> None:
    # -cap -> 0, 0 -> 0.5, +cap -> 1.0.
    assert _normalize_momentum_zscore(0.0) == 0.5
    assert _normalize_momentum_zscore(-3.0) == 0.0
    assert _normalize_momentum_zscore(3.0) == 1.0
    assert _normalize_momentum_zscore(1.0) > 0.5  # positive momentum favored


def test_normalize_momentum_zscore_symmetric() -> None:
    # Symmetric around 0: +1 and -1 are equal distance from 0.5.
    up = _normalize_momentum_zscore(1.0)
    down = _normalize_momentum_zscore(-1.0)
    assert up == pytest.approx(1.0 - down)


def test_candidate_factors_only_known_feature_names() -> None:
    feats = [
        _feature("liquidity_growth_pct_1h", 0.5),
        _feature("price_momentum_zscore_1h", 1.0),
        _feature("holder_count_delta_1h", 9.9),  # has a valid suffix, not in config
    ]
    factors = _candidate_factors(feats)
    names = {f.name for f in factors}
    assert names == {"liquidity_growth_pct_1h", "price_momentum_zscore_1h"}
    # Deterministic order by name.
    assert [f.name for f in factors] == sorted(f.name for f in factors)


def test_candidate_factors_contribution_is_value_times_weight() -> None:
    feats = [_feature("liquidity_growth_pct_1h", 0.5)]
    factors = _candidate_factors(feats)
    f = factors[0]
    assert f.contribution == f.value * f.weight
    assert f.weight == cfg.FEATURE_WEIGHTS["liquidity_growth_pct_1h"]


def test_normalize_and_factor_deterministic() -> None:
    """Same inputs -> identical factors (no randomness, DOC-013)."""
    feats = [_feature("liquidity_growth_pct_1h", 0.5), _feature("price_momentum_zscore_1h", 1.0)]
    a = _candidate_factors(feats)
    b = _candidate_factors(feats)
    assert [(f.name, f.value, f.weight, f.contribution) for f in a] == [
        (f.name, f.value, f.weight, f.contribution) for f in b
    ]


def test_ranking_config_version_present() -> None:
    assert cfg.RANKING_RULES_VERSION == "1.0"
    assert "liquidity_growth_pct_1h" in cfg.FEATURE_WEIGHTS
    assert cfg.MIN_FEATURES_REQUIRED >= 1
