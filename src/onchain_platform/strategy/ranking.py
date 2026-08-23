"""Strategy — candidate ranking (DOC-009 § Strategy).

Deterministic, rule-based ranking of research candidates. Strategy
"recommends what deserves further investigation" (DOC-009); it does not
execute trades, manage portfolios, or allocate capital.

Determinism (DOC-013 § Determinism Discipline): NO wall-clock inside this
module — `as_of` is injected by the caller (`None` is forwarded to the
persistence layer, which owns its own PIT default). No set iteration on the
ranking path; candidates are collected into a list and sorted deterministically
by (score DESC, canonical_id ASC).

Import-linter: `strategy/` may NOT import `analytics/` or `intelligence/`.
The engine reads Features / Insights / Outcomes through the `persistence/`
repositories (cross-cutting infra, allowed) and consumes domain schemas —
nothing above the persistence layer.

Explainability (DOC-001): every RankedCandidate carries a per-factor
contribution list so a researcher sees exactly why a pair ranked where it did.
"""

from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.schemas.enums import OutcomeType
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.domain.schemas.ranking import RankedCandidate, RankingFactor
from onchain_platform.persistence.postgres import entity_repositories as entity_repo
from onchain_platform.persistence.postgres.outcomes_insights import (
    get_latest_insight,
    get_latest_outcome,
)
from onchain_platform.persistence.timescale import repositories as ts_repo
from onchain_platform.strategy import ranking_config as cfg

logger = structlog.get_logger(__name__)


@dataclass
class _ScoredCandidate:
    """Internal pre-rank holder (rank assigned after sorting)."""

    pair_id: str
    score: float
    factors: list[RankingFactor] = field(default_factory=list)


def _normalize_liquidity_growth(value: float) -> float:
    """Map a liquidity_growth_pct (0.5 = 50% growth) into a 0..1 sub-score."""
    if cfg.LIQUIDITY_GROWTH_CAP <= 0:
        return 0.0
    capped = min(abs(float(value)), cfg.LIQUIDITY_GROWTH_CAP)
    return capped / cfg.LIQUIDITY_GROWTH_CAP


def _normalize_momentum_zscore(value: float) -> float:
    """Map a price_momentum_zscore into a 0..1 sub-score.

    Momentum is signed (negative → down). We score directional interest:
    positive momentum is a stronger research signal than negative, mapping
    [-cap, +cap] -> [0.0, 1.0] (0 at -cap, 1 at +cap). Symmetric so a pair
    that moved sharply down still scores < 0.5.
    """
    cap = cfg.MOMENTUM_ZSCORE_CAP
    if cap <= 0:
        return 0.0
    capped = max(-cap, min(float(value), cap))
    return (capped + cap) / (2 * cap)


def _normalize_feature(name: str, value: float) -> float:
    """Dispatch a raw Feature value to a 0..1 sub-score by name."""
    if name == "liquidity_growth_pct_1h":
        return _normalize_liquidity_growth(value)
    if name == "price_momentum_zscore_1h":
        return _normalize_momentum_zscore(value)
    # Unknown name (not in config): clamp magnitude to [0,1] defensively.
    return min(max(abs(float(value)), 0.0), 1.0)


def _candidate_factors(features: list[Feature]) -> list[RankingFactor]:
    """Build deterministic RankingFactors for a pair's usable features, using
    only feature names present in FEATURE_WEIGHTS."""
    factors: list[RankingFactor] = []
    for feat in features:
        name = feat.feature_name
        weight = cfg.FEATURE_WEIGHTS.get(name)
        if weight is None:
            continue
        norm = _normalize_feature(name, feat.value)
        factors.append(
            RankingFactor(name=name, value=norm, weight=weight, contribution=norm * weight)
        )
    # Deterministic order (DOC-013): by factor name.
    factors.sort(key=lambda f: f.name)
    return factors


async def _signals(session: AsyncSession, entity_id: str) -> tuple[float, float]:
    """Return (risk_penalty, outcome_bonus) for a candidate.

    Risk: a HoneypotDetected insight adds a fixed penalty.
    Outcome: a SUCCESSFUL_LAUNCH with label_value true adds a boost; a
    RUG_PULL with label_value true adds a penalty. Sparse — safe when absent.
    """
    penalty = 0.0
    bonus = 0.0

    honey = await get_latest_insight(session, entity_id, cfg.HONEYPOT_INSIGHT_TYPE)
    if honey is not None:
        penalty += cfg.RISK_PENALTY_HONEYPOT

    launch = await get_latest_outcome(session, entity_id, OutcomeType.SUCCESSFUL_LAUNCH)
    if launch is not None and launch.label_value:
        bonus += cfg.SUCCESSFUL_LAUNCH_BOOST

    rug = await get_latest_outcome(session, entity_id, OutcomeType.RUG_PULL)
    if rug is not None and rug.label_value:
        penalty += cfg.RUG_PULL_PENALTY

    return penalty, bonus


async def _rank_one_pair(
    session: AsyncSession, pair: TradingPair, as_of: datetime
) -> _ScoredCandidate | None:
    """Rank a single pair, or None if it lacks enough usable features."""
    features = await ts_repo.list_latest_features(session, pair.canonical_id, as_of)
    factors = _candidate_factors(features)
    if len(factors) < cfg.MIN_FEATURES_REQUIRED:
        return None

    base = sum(f.contribution for f in factors)
    penalty, bonus = await _signals(session, pair.canonical_id)
    score = base - penalty + bonus
    return _ScoredCandidate(pair_id=pair.canonical_id, score=score, factors=factors)


async def compute_ranking(
    session: AsyncSession,
    *,
    chain_id: int | None = None,
    dex: str | None = None,
    limit: int = 50,
    as_of: datetime,
) -> list[RankedCandidate]:
    """Compute a deterministic ranking of research candidates (DOC-009).

    Reads candidate pairs (filters), their latest known Features (PIT), and
    per-candidate risk/outcome signals. Returns the top ``limit`` candidates
    sorted by (score DESC, canonical_id ASC). Deterministic: same inputs →
    same ranking, with explainable per-factor contributions (DOC-001).

    `as_of` is REQUIRED and injected by the caller (the API router resolves
    "now" at its own boundary — DOC-013: the strategy engine may not read the
    wall clock).
    """
    pairs, _ = await entity_repo.list_pairs(session, chain_id=chain_id, dex=dex)

    scored: list[_ScoredCandidate] = []
    for pair in sorted(pairs, key=lambda p: p.canonical_id):
        res = await _rank_one_pair(session, pair, as_of)
        if res is not None:
            scored.append(res)

    scored.sort(key=lambda c: (-c.score, c.pair_id))

    ranked: list[RankedCandidate] = []
    for idx, raw in enumerate(scored[:limit], start=1):
        ranked.append(
            RankedCandidate(
                pair_id=raw.pair_id,
                score=raw.score,
                rank=idx,
                factors=raw.factors,
            )
        )
    return ranked
