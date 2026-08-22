"""Unit tests: Outcome rules (DOC-012 § B.4, DOC-013 Determinism).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions). Rules are pure functions — same inputs → same label_value.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from onchain_platform.analytics import outcome_rules
from onchain_platform.domain.schemas.enums import BarInterval
from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot

PINNED = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
ENTITY_ID = "eip155:8453/pair:0xabc"
CHAIN_ID = 8453


def _make_snap(ts: datetime, reserve0: str, reserve1: str) -> ObservationSnapshot:
    return ObservationSnapshot.create(
        entity_id=ENTITY_ID,
        chain_id=CHAIN_ID,
        snapshot_timestamp=ts,
        observed_at=ts,
        ingested_at=ts,
        source="test",
        reserve0=reserve0,
        reserve1=reserve1,
        price=str(Decimal(reserve1) / Decimal(reserve0)) if Decimal(reserve0) > 0 else "0",
    )


def _bar(ts: datetime, trade_count: int) -> MarketBar:
    return MarketBar.create(
        pair_id=ENTITY_ID,
        chain_id=CHAIN_ID,
        interval=BarInterval.ONE_MINUTE,
        bar_start_time=ts,
        open_="1",
        high="1",
        low="1",
        close="1",
        volume_base="0",
        volume_quote="0",
        trade_count=trade_count,
        vwap="1",
        buy_volume="0",
        sell_volume="0",
        source_fact_range=("f1", "f2"),
        is_provisional=False,
        computed_at=PINNED,
    )


# ---------------------------------------------------------------------------
# RUG_PULL
# ---------------------------------------------------------------------------


def test_rug_pull_honeypot_true_overrides_everything() -> None:
    snapshots = [_make_snap(PINNED, "100", "100"), _make_snap(PINNED, "100", "100")]
    bars = [_bar(PINNED, 5)]
    # Honeypot is an auto-fail regardless of reserve math (ANY logic).
    assert outcome_rules.evaluate_rug_pull(snapshots, bars, is_honeypot=True) is True


def test_rug_pull_liquidity_collapse_gt_90_percent() -> None:
    # early reserve product = 100*100 = 10000, late = 10*10 = 100 → drop 99%.
    early = _make_snap(PINNED, "100", "100")
    late = _make_snap(PINNED, "10", "10")
    bars = [_bar(PINNED, 1)]
    assert outcome_rules.evaluate_rug_pull([early, late], bars) is True


def test_rug_pull_liquidity_drop_below_threshold_false() -> None:
    # drop = (10000 - 9000)/10000 = 10% → not a rug pull.
    early = _make_snap(PINNED, "100", "100")
    late = _make_snap(PINNED, "90", "100")
    bars = [_bar(PINNED, 1)]
    assert outcome_rules.evaluate_rug_pull([early, late], bars) is False


def test_rug_pull_insufficient_snapshots_false() -> None:
    # Single snapshot → cannot measure a drop.
    bars = [_bar(PINNED, 1)]
    assert outcome_rules.evaluate_rug_pull([_make_snap(PINNED, "100", "100")], bars) is False


def test_rug_pull_zero_early_reserve_guard_false() -> None:
    early = _make_snap(PINNED, "0", "100")  # early product == 0 → cannot compute
    late = _make_snap(PINNED, "10", "10")
    bars = [_bar(PINNED, 1)]
    assert outcome_rules.evaluate_rug_pull([early, late], bars) is False


# ---------------------------------------------------------------------------
# SUCCESSFUL_LAUNCH
# ---------------------------------------------------------------------------


def test_successful_launch_all_conditions_met() -> None:
    # 50 trades, no honeypot, late reserve >= 70% of peak.
    snapshots = [
        _make_snap(PINNED, "100", "100"),  # peak 10000
        _make_snap(PINNED, "90", "100"),  # late 9000 ≥ 0.7*10000
    ]
    bars = [_bar(PINNED, 50)]
    assert outcome_rules.evaluate_successful_launch(snapshots, bars, is_honeypot=False) is True


def test_successful_launch_honeypot_false() -> None:
    snapshots = [_make_snap(PINNED, "100", "100"), _make_snap(PINNED, "100", "100")]
    bars = [_bar(PINNED, 50)]
    assert outcome_rules.evaluate_successful_launch(snapshots, bars, is_honeypot=True) is False


def test_successful_launch_below_min_trades_false() -> None:
    snapshots = [_make_snap(PINNED, "100", "100"), _make_snap(PINNED, "100", "100")]
    bars = [_bar(PINNED, 29)]  # < 30 trades
    assert outcome_rules.evaluate_successful_launch(snapshots, bars) is False


def test_successful_launch_liquidity_not_survived_false() -> None:
    # Reserve product collapsed below 70% of peak → not a successful launch.
    snapshots = [
        _make_snap(PINNED, "100", "100"),  # peak 10000
        _make_snap(PINNED, "30", "100"),  # late 3000 < 0.7*10000
    ]
    bars = [_bar(PINNED, 50)]
    assert outcome_rules.evaluate_successful_launch(snapshots, bars) is False


def test_successful_launch_no_snapshots_false() -> None:
    bars = [_bar(PINNED, 50)]
    assert outcome_rules.evaluate_successful_launch([], bars) is False


# ---------------------------------------------------------------------------
# DEAD_TOKEN
# ---------------------------------------------------------------------------


def test_dead_token_zero_trades_true() -> None:
    snapshots = [_make_snap(PINNED, "100", "100"), _make_snap(PINNED, "100", "100")]
    bars: list[MarketBar] = []  # zero swaps in window
    assert outcome_rules.evaluate_dead_token(snapshots, bars) is True


def test_dead_token_reserves_drained_true() -> None:
    snapshots = [
        _make_snap(PINNED, "100", "100"),
        _make_snap(PINNED, "0", "0"),  # drained
    ]
    bars = [_bar(PINNED, 20)]  # some activity but liquidity gone
    assert outcome_rules.evaluate_dead_token(snapshots, bars) is True


def test_dead_token_active_and_solvent_false() -> None:
    snapshots = [_make_snap(PINNED, "100", "100"), _make_snap(PINNED, "90", "100")]
    bars = [_bar(PINNED, 20)]
    assert outcome_rules.evaluate_dead_token(snapshots, bars) is False


def test_dead_token_no_snapshots_and_no_trades_true() -> None:
    # Empty window: no trades AND no reserves → dead by zero-activity rule.
    assert outcome_rules.evaluate_dead_token([], []) is True


# ---------------------------------------------------------------------------
# Determinism + dispatch
# ---------------------------------------------------------------------------


def test_rules_deterministic_same_inputs_same_output() -> None:
    snapshots = [  # collapse case
        _make_snap(PINNED, "100", "100"),
        _make_snap(PINNED, "10", "10"),
    ]
    bars = [_bar(PINNED, 1)]
    r1 = outcome_rules.evaluate_rug_pull(snapshots, bars)
    r2 = outcome_rules.evaluate_rug_pull(snapshots, bars)
    assert r1 is r2


def test_dispatch_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown outcome_type"):
        outcome_rules.evaluate_for_type("NOT_A_LABEL", [], [], False)


def test_label_definition_version_constant() -> None:
    assert outcome_rules.OUTCOME_RULES_VERSION == "1.0"


def test_label_definition_for_all_types() -> None:
    for t in ("RUG_PULL", "SUCCESSFUL_LAUNCH", "DEAD_TOKEN"):
        assert outcome_rules.label_definition_for(t)


def test_parse_observation_window() -> None:
    # Parse helper lives in the engine; rules module exposes durations via
    # the constants referenced in label_definition. Smoke assertions:
    import onchain_platform.analytics.outcome_engine as engine_mod

    assert engine_mod.parse_observation_window("1h") == 3600
    assert engine_mod.parse_observation_window("24h") == 86400
    assert engine_mod.parse_observation_window("7d") == 7 * 86400
