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

Rules are versioned via OUTCOME_RULES_VERSION; historical Outcomes keep
their original version forever (DOC-012 § B.4).
"""

from decimal import Decimal

from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot

# Versioned for reproducibility — historical scores remain explainable and
# are never rewritten when rules evolve (DOC-012 § B.4).
OUTCOME_RULES_VERSION = "1.0"

# Rule thresholds (V1, deterministic).
RUG_PULL_LIQUIDITY_DROP_PCT = Decimal("0.90")  # early→late reserve-product drop > 90%
SUCCESSFUL_LAUNCH_MIN_TRADES = 30
SUCCESSFUL_LAUNCH_RESERVE_SURVIVAL = Decimal("0.70")  # late >= 70% of window peak


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
) -> bool:
    """RUG_PULL (V1, logic = ANY):
    (a) honeypot detected (from persisted insight), OR
    (b) early→late reserve-product drop > 90% (liquidity collapse)."""
    if is_honeypot:
        return True
    drop = _liquidity_drop_pct(snapshots)
    return drop is not None and drop > RUG_PULL_LIQUIDITY_DROP_PCT


def evaluate_successful_launch(
    snapshots: list[ObservationSnapshot],
    bars: list[MarketBar],
    is_honeypot: bool = False,
) -> bool:
    """SUCCESSFUL_LAUNCH (V1, logic = ALL):
    (a) NOT honeypot, AND
    (b) total trades over the window >= 30, AND
    (c) reserve product at window end >= 70% of its peak within the window."""
    if is_honeypot:
        return False
    if _total_trades(bars) < SUCCESSFUL_LAUNCH_MIN_TRADES:
        return False
    late = _late_product(snapshots)
    peak = revisit_peak_product(snapshots)
    # Cannot assert liquidity survived if we have no reserve readings.
    if late is None or peak is None or peak == 0:
        return False
    return late >= SUCCESSFUL_LAUNCH_RESERVE_SURVIVAL * peak


def evaluate_dead_token(
    snapshots: list[ObservationSnapshot],
    bars: list[MarketBar],
) -> bool:
    """DEAD_TOKEN (V1, logic = ANY):
    (a) zero swaps across the entire window, OR
    (b) reserves fully drained (late reserve product == 0)."""
    if _total_trades(bars) == 0:
        return True
    late = _late_product(snapshots)
    return late is not None and late == 0


def label_definition_for(outcome_type: str) -> str:
    """Human-readable rule description for the versioned OUTCOME_RULES_VERSION."""
    return {
        "RUG_PULL": (
            f"Liquidity drop >{int(RUG_PULL_LIQUIDITY_DROP_PCT * 100)}% "
            "(reserve0×reserve1) within observation window OR honeypot detected"
        ),
        "SUCCESSFUL_LAUNCH": (
            f">={SUCCESSFUL_LAUNCH_MIN_TRADES} trades, no honeypot, and reserve "
            f"product retained >={int(SUCCESSFUL_LAUNCH_RESERVE_SURVIVAL * 100)}% of peak"
        ),
        "DEAD_TOKEN": ("zero trades across the observation window OR reserve product drained to 0"),
    }[outcome_type]


def evaluate_for_type(
    outcome_type: str,
    snapshots: list[ObservationSnapshot],
    bars: list[MarketBar],
    is_honeypot: bool,
) -> bool:
    """Dispatch to the versioned rule for one outcome type.

    Deterministic fixed-order dispatch (DOC-013 § Determinism Discipline).
    """
    if outcome_type == "RUG_PULL":
        return evaluate_rug_pull(snapshots, bars, is_honeypot)
    if outcome_type == "SUCCESSFUL_LAUNCH":
        return evaluate_successful_launch(snapshots, bars, is_honeypot)
    if outcome_type == "DEAD_TOKEN":
        return evaluate_dead_token(snapshots, bars)
    raise ValueError(f"unknown outcome_type: {outcome_type}")
