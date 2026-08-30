"""Outcome rules — deterministic, versioned rule evaluation (DOC-012 § B.4).

The rule engine produces ground-truth labels from PIT-correct input data:
Observation Snapshots (reserves) + Market Bars (trade activity) + a caller
supplied honeypot flag (read from the persisted insights table by the
Outcome Engine, NOT from int ement/ — analytics/ may not import
intelligence/ per DOC-011).

All rules are pure functions: no I/O, no wall-clock, no set iteration, no
unseeded randomness (DOC-013 § Determinism Discipline). Same inputs →
same label_value, always.

`liquidity_usd` is NULL in the MVP (no price oracle, M7 gap), so reserve
depth is measured by the `reserve0 * reserve1` product as a deterministic
proxy (DOC-014). This is a documented limitation, not a USD valuation.

Thresholds are window-aware (Phase 0 Step 2 — ML Foundation prerequisite):
a 90% liquidity drop in 1h is a flash-crash rug pull, but over 24h a 70%
drop is already concerning (a slow bleed). `get_thresholds(observation_window)`
returns the configuration for the window; the 1h config is identical to the
pre-existing V1 constants, so existing behaviour is unchanged.

Rules are versioned via OUTCOME_RULES_VERSION; historical Outcomes keep
their original version forever (DOC-012 § B.4).
"""

from dataclasses import dataclass
from decimal import Decimal

from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot

# Versioned for reproducibility — historical scores remain explainable and
# are never rewritten when rules evolve (DOC-012 § B.4).
OUTCOME_RULES_VERSION = "1.0"


@dataclass(frozen=True)
class ThresholdConfig:
    """Window-specific thresholds for outcome evaluation.

    Different observation windows apply the same rule structure but with
    different numeric bounds. For example, a 90% liquidity drop in 1 hour is
    a flash crash / rug pull, while a 70% drop across 24 hours is a slow
    bleed that is equally (or more) concerning for a liquidity-backed pair.

    Attributes mirror the V1 rule constants; the 1h config equals the
    original hardcoded values, so enabling a new window never changes how the
    existing 1h window is scored.
    """

    liquidity_drop_threshold: Decimal  # e.g. 0.90 → drop >90% flags RUG_PULL
    success_min_trades: int  # e.g. 30 trades in window for SUCCESSFUL_LAUNCH
    success_liquidity_retention: Decimal  # e.g. 0.70 → late >=70% of peak
    dead_token_swap_threshold: int  # e.g. <=N trades in window → DEAD_TOKEN


WINDOW_THRESHOLDS: dict[str, ThresholdConfig] = {
    "1h": ThresholdConfig(
        liquidity_drop_threshold=Decimal("0.90"),  # 90% in 1h = flash crash
        success_min_trades=30,
        success_liquidity_retention=Decimal("0.70"),
        dead_token_swap_threshold=0,
    ),
    "24h": ThresholdConfig(
        liquidity_drop_threshold=Decimal("0.70"),  # 70% in 24h = slow bleed
        success_min_trades=30,
        success_liquidity_retention=Decimal("0.50"),  # more lenient over 24h
        dead_token_swap_threshold=5,  # a few swaps over 24h still reads dead
    ),
}


def get_thresholds(observation_window: str) -> ThresholdConfig:
    """Return the threshold configuration for an observation window.

    Args:
        observation_window: e.g. "1h", "24h".

    Returns:
        ThresholdConfig for the window.

    Raises:
        ValueError: if the observation_window is not recognized.
    """
    try:
        return WINDOW_THRESHOLDS[observation_window]
    except KeyError as exc:
        raise ValueError(f"Unknown observation window: {observation_window}") from exc


def _reserve_product(snapshot: ObservationSnapshot) -> Decimal:
    """Liquidity-depth proxy: reserve0 × reserve1 (Decimal math, DOC-008).

    NOT a USD valuation — `liquidity_usd` is NULL in the MVP; this product is
    the deterministic depth proxy M7 already relied on.
    """
    return Decimal(snapshot.reserve0) * Decimal(snapshot.reserve1)


def _total_trades(bars: list[MarketBar]) -> int:
    """Sum of trade_count across all bars in the (PIT-filtered) window."""
    return sum(bar.trade_count for bar in bars)


def _liquidity_drop_pct(snapshots: list[ObservationSnapshot]) -> Decimal | None:
    """Early→late reserve-product drop as a proportion, or None if the window
    lacks the snapshots needed to compute it (early < 2 snapshots or early
    product == 0)."""
    if len(snapshots) < 2:
        return None
    early = _reserve_product(snapshots[0])  # list is already PIT-ordered
    if early == 0:
        return None  # cannot measure a drop from zero depth
    late = _reserve_product(snapshots[-1])
    return (early - late) / early


def _late_product(snapshots: list[ObservationSnapshot]) -> Decimal | None:
    """Reserve product of the latest snapshot in the window, or None if empty."""
    if not snapshots:
        return None
    return _reserve_product(snapshots[-1])


def revisit_peak_product(snapshots: list[ObservationSnapshot]) -> Decimal | None:
    """Peak reserve product within the window, or None if no snapshots."""
    if not snapshots:
        return None
    return max(_reserve_product(s) for s in snapshots)


def evaluate_rug_pull(
    snapshots: list[ObservationSnapshot],
    bars: list[MarketBar],
    is_honeypot: bool = False,
    observation_window: str = "1h",
) -> bool:
    """RUG_PULL (logic = ANY):
    (a) honeypot detected (from persisted insight), OR
    (b) early→late reserve-product drop > window threshold (default 90%)."""
    if is_honeypot:
        return True
    thresholds = get_thresholds(observation_window)
    drop = _liquidity_drop_pct(snapshots)
    return drop is not None and drop > thresholds.liquidity_drop_threshold


def evaluate_successful_launch(
    snapshots: list[ObservationSnapshot],
    bars: list[MarketBar],
    is_honeypot: bool = False,
    observation_window: str = "1h",
) -> bool:
    """SUCCESSFUL_LAUNCH (logic = ALL):
    (a) NOT honeypot, AND
    (b) total trades over the window >= threshold (default 30), AND
    (c) reserve product at window end >= retention threshold of its peak
    (default 70%)."""
    if is_honeypot:
        return False
    thresholds = get_thresholds(observation_window)
    if _total_trades(bars) < thresholds.success_min_trades:
        return False
    late = _late_product(snapshots)
    peak = revisit_peak_product(snapshots)
    # Cannot assert liquidity survived if we have no reserve readings.
    if late is None or peak is None or peak == 0:
        return False
    return late >= thresholds.success_liquidity_retention * peak


def evaluate_dead_token(
    snapshots: list[ObservationSnapshot],
    bars: list[MarketBar],
    observation_window: str = "1h",
) -> bool:
    """DEAD_TOKEN (logic = ANY):
    (a) trades across the window <= threshold (default 0 / zero swaps), OR
    (b) reserves fully drained (late reserve product == 0)."""
    thresholds = get_thresholds(observation_window)
    if _total_trades(bars) <= thresholds.dead_token_swap_threshold:
        return True
    late = _late_product(snapshots)
    return late is not None and late == 0


def label_definition_for(outcome_type: str, observation_window: str = "1h") -> str:
    """Human-readable rule description for the versioned OUTCOME_RULES_VERSION.

    Window-aware: reflects the threshold applied for the given observation
    window, so a 24h label's definition accurately states its 70% rule rather
    than copying the 1h 90% text.
    """
    t = get_thresholds(observation_window)
    return {
        "RUG_PULL": (
            f"Liquidity drop >{int(t.liquidity_drop_threshold * 100)}% "
            "(reserve0×reserve1) within observation window OR honeypot detected"
        ),
        "SUCCESSFUL_LAUNCH": (
            f">={t.success_min_trades} trades, no honeypot, and reserve "
            f"product retained >={int(t.success_liquidity_retention * 100)}% of peak"
        ),
        "DEAD_TOKEN": (
            f"<={t.dead_token_swap_threshold} trades across the observation "
            "window OR reserve product drained to 0"
        ),
    }[outcome_type]


def evaluate_for_type(
    outcome_type: str,
    snapshots: list[ObservationSnapshot],
    bars: list[MarketBar],
    is_honeypot: bool,
    observation_window: str = "1h",
) -> bool:
    """Dispatch to the versioned rule for one outcome type.

    Deterministic fixed-order dispatch (DOC-013 § Determinism Discipline).
    The observation window selects the threshold configuration (1h default,
    preserving the original constants).
    """
    if outcome_type == "RUG_PULL":
        return evaluate_rug_pull(snapshots, bars, is_honeypot, observation_window)
    if outcome_type == "SUCCESSFUL_LAUNCH":
        return evaluate_successful_launch(snapshots, bars, is_honeypot, observation_window)
    if outcome_type == "DEAD_TOKEN":
        return evaluate_dead_token(snapshots, bars, observation_window)
    raise ValueError(f"unknown outcome_type: {outcome_type}")
