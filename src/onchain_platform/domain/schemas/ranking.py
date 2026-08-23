"""Ranking schemas for candidate ranking (DOC-009 § Strategy).

Strategy "recommends what deserves further investigation" — it does not
execute trades or manage portfolios (DOC-009). These schemas carry the
deterministic, explainable output of the ranking engine: one candidate's
total weighted score plus the per-factor contributions that produced it
(DOC-001 "Explainable").

Frozen like every Canonical Schema (DOC-013 § Immutability & State
Modeling). No confidence field — confidence belongs to Prediction, not
ranking output. There is deliberately no outcome label or override here:
Strategy recommends based on features/risk/outcome signals, it does not
re-assert ground truth.
"""

from pydantic import BaseModel, ConfigDict, Field


class RankingFactor(BaseModel):
    """One factor contributing to a candidate's rank (DOC-001 Explainable).

    `value` is the normalized feature/risk sub-score (bounded), `weight`
    comes from strategy/ranking_config.py, and `contribution = value * weight`.
    """

    model_config = ConfigDict(frozen=True)

    name: str  # e.g. "liquidity_growth_pct_1h"
    value: float  # raw normalized value (bounded)
    weight: float  # weight from ranking_config
    contribution: float = Field(description="value * weight")


class RankedCandidate(BaseModel):
    """A ranked candidate with explainable factors (DOC-009 Strategy).

    `pair_id` is the canonical TradingPair ID; `score` is the deterministic
    weighted total; `rank` is its 1-based position; `factors` explains why.
    """

    model_config = ConfigDict(frozen=True)

    pair_id: str  # Canonical ID e.g. eip155:8453/pair:0x...
    score: float  # total weighted score
    rank: int = Field(ge=1)  # 1 = top candidate
    factors: list[RankingFactor] = Field(default_factory=list)
