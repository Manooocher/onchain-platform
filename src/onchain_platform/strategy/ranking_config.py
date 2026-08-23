"""Ranking configuration — versioned weights and thresholds (DOC-009 Strategy).

Python constants (not YAML) per M10 Q5: type-safe, IDE-friendly, no parsing
step, and trivially versioned. Ranking is deterministic: weights are fixed
module constants; nothing here reads the clock or random state (DOC-013
Determinism Discipline).

Weights are an MVP baseline, versioned for reproducibility. They are
deliberately simple — Strategy recommends, it does not act (DOC-009), so the
ranking is a transparent, explainable heuristic that a researcher can audit
and tune.
"""

# Versioned for reproducibility — a ranking produced under this version is
# reproducible forever, and weights can be revised without rewriting history.
RANKING_RULES_VERSION = "1.0"

# Feature weights (name → weight). A feature that exists for a pair but is
# not listed here is not part of the ranking (ignored). Sum of weights need
# not be 1 — scores are bounding-normalized before summing.
FEATURE_WEIGHTS: dict[str, float] = {
    "liquidity_growth_pct_1h": 0.35,
    "price_momentum_zscore_1h": 0.30,
}

# Cap used to normalize a raw feature value into a 0..1 sub-score. Any value
# at or above the cap maps to 1.0 (avoids a single runaway feature dominating
# the ranking). Liquidity growth is a percentage (e.g. 0.5 = 50% growth);
# momentum is a z-score (usual range roughly -3..3).
LIQUIDITY_GROWTH_CAP = 1.0  # 100% growth over the window saturates
MOMENTUM_ZSCORE_CAP = 3.0  # |z| >= 3 saturates

# Risk penalty: a HoneypotDetected insight subtracts this from the score.
RISK_PENALTY_HONEYPOT = 0.5

# Outcome bump: a labeled outcome worth boosting? Currently:
# - If the latest SUCCESSFUL_LAUNCH outcome has label_value true, add a small
#   positive bump to surface validated launches.
# - If the latest RUG_PULL outcome label_value is true, apply a penalty.
# These are conditional signals (sparse — most pairs have no closed outcome).
SUCCESSFUL_LAUNCH_BOOST = 0.15
RUG_PULL_PENALTY = 0.4

# A pair must have at least this many usable (known-name) features before it
# is ranked at all; otherwise it is omitted from the candidate list (a pair
# with no signal is not a candidate — DOC-009 Strategy filters opportunities).
MIN_FEATURES_REQUIRED = 1

# The insight_type string emitted by intelligence/insight_generator.py for a
# honeypot (M8 already reads this from the persisted insights table).
HONEYPOT_INSIGHT_TYPE = "HoneypotDetected"
