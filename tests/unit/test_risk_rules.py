"""Unit tests: Risk Rules Engine (Milestone 7).

Deterministic: same inputs → same outputs (DOC-013 § Determinism
Discipline). All weights are hardcoded and versioned.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from onchain_platform.domain.schemas.risk_signals import RiskSignals
from onchain_platform.intelligence.risk_rules import (
    RISK_RULES_VERSION,
    compute_risk_score,
    extract_risk_signals,
    identify_risk_indicators,
)


def test_extract_risk_signals_from_goplus_response() -> None:
    goplus: dict[str, object] = {
        "is_honeypot": "1",
        "sell_tax": "0.25",
        "hidden_owner": "1",
        "is_open_source": "1",
        "holder_count": "1000",
    }
    signals = extract_risk_signals(goplus)
    assert signals.is_honeypot == "1"
    assert signals.sell_tax == "0.25"
    assert signals.hidden_owner == "1"
    assert signals.is_open_source == "1"
    assert signals.holder_count == "1000"
    assert signals.risk_score == 1.0  # honeypot auto-fail
    assert "Honeypot Detected" in signals.risk_indicators
    assert signals.risk_rules_version == RISK_RULES_VERSION


def test_honeypot_auto_fail() -> None:
    signals = RiskSignals(is_honeypot="1", risk_score=0.0)
    score = compute_risk_score(signals)
    assert score == 1.0


def test_clean_token_low_score() -> None:
    signals = RiskSignals(
        is_open_source="1",
        is_honeypot="0",
        hidden_owner="0",
        is_mintable="0",
        sell_tax="0",
        buy_tax="0",
        risk_score=0.0,
    )
    score = compute_risk_score(signals)
    assert score == 0.0


def test_hidden_owner_adds_weight() -> None:
    signals = RiskSignals(hidden_owner="1", risk_score=0.0)
    score = compute_risk_score(signals)
    assert score > 0.0
    indicators = identify_risk_indicators(signals)
    assert "Hidden Owner" in indicators


def test_high_sell_tax_detected() -> None:
    signals = RiskSignals(sell_tax="0.25", risk_score=0.0)
    indicators = identify_risk_indicators(signals)
    assert any("High Sell Tax" in ind for ind in indicators)
    score = compute_risk_score(signals)
    assert score > 0.0


def test_blacklist_detected() -> None:
    signals = RiskSignals(is_blacklisted="1", risk_score=0.0)
    indicators = identify_risk_indicators(signals)
    assert "Blacklist Function" in indicators
    score = compute_risk_score(signals)
    assert score >= 0.6


def test_risk_score_capped_at_one() -> None:
    # Multiple high-weight indicators should cap at 1.0.
    signals = RiskSignals(
        is_blacklisted="1",
        is_airdrop_scam="1",
        fake_token="1",
        risk_score=0.0,
    )
    score = compute_risk_score(signals)
    assert score == 1.0


def test_risk_score_deterministic() -> None:
    """Same inputs → same outputs, 100 iterations (DOC-013 § Determinism)."""
    goplus: dict[str, object] = {
        "is_honeypot": "0",
        "hidden_owner": "1",
        "sell_tax": "0.3",
        "is_blacklisted": "0",
    }
    scores = []
    for _ in range(100):
        signals = extract_risk_signals(goplus)
        scores.append(signals.risk_score)
    assert len(set(scores)) == 1  # all identical


def test_none_fields_handled_gracefully() -> None:
    """Missing GoPlus fields → None, no crash."""
    goplus: dict[str, object] = {}
    signals = extract_risk_signals(goplus)
    assert signals.is_honeypot is None
    assert signals.sell_tax is None
    assert signals.risk_score == 0.0
    assert signals.risk_indicators == []
