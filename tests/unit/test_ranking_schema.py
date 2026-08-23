"""Unit tests: Ranking schema (DOC-009 § Strategy, DOC-013 Immutability).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 Testing
Conventions).
"""

import pytest
from pydantic import ValidationError

from onchain_platform.domain.schemas.ranking import RankedCandidate, RankingFactor


def _make_factor(
    *,
    name: str = "liquidity_growth_pct_1h",
    value: float = 0.8,
    weight: float = 0.5,
) -> RankingFactor:
    return RankingFactor(name=name, value=value, weight=weight, contribution=value * weight)


def test_ranking_factor_round_trip() -> None:
    f = _make_factor()
    restored = RankingFactor.model_validate(f.model_dump())
    assert restored == f
    assert restored.contribution == pytest.approx(0.8 * 0.5)


def test_ranked_candidate_round_trip_with_factors() -> None:
    factors = [_make_factor(), _make_factor(name="price_momentum_zscore_1h", value=1.5, weight=0.3)]
    cand = RankedCandidate(pair_id="eip155:8453/pair:0xabc", score=1.05, rank=1, factors=factors)
    restored = RankedCandidate.model_validate(cand.model_dump())
    assert restored == cand
    assert len(restored.factors) == 2
    assert restored.factors[1].name == "price_momentum_zscore_1h"


def test_ranked_candidate_frozen_rejects_mutation() -> None:
    cand = RankedCandidate(pair_id="p1", score=1.0, rank=1)
    with pytest.raises(ValidationError):
        cand.score = 2.0  # type: ignore[misc]


def test_ranking_factor_frozen_rejects_mutation() -> None:
    f = _make_factor()
    with pytest.raises(ValidationError):
        f.weight = 0.9  # type: ignore[misc]


def test_rank_must_be_ge_one() -> None:
    with pytest.raises(ValidationError):
        RankedCandidate(pair_id="p1", score=1.0, rank=0)


def test_ranked_candidate_default_factors_empty() -> None:
    cand = RankedCandidate(pair_id="p1", score=0.0, rank=1)
    assert cand.factors == []
