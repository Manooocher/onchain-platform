"""Unit tests: Insight Generator (Milestone 7).

Deterministic: same inputs → same Insights (DOC-013 § Determinism
Discipline).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from onchain_platform.domain.schemas.enums import Importance
from onchain_platform.domain.schemas.risk_signals import RiskSignals
from onchain_platform.intelligence.insight_generator import generate_insights

ENTITY_ID = "eip155:8453/pair:0xabc"


def test_honeypot_insight_high_importance() -> None:
    signals = RiskSignals(is_honeypot="1", risk_score=1.0)
    insights = generate_insights(ENTITY_ID, signals)
    assert len(insights) >= 1
    honeypot = [i for i in insights if i.insight_type == "HoneypotDetected"]
    assert len(honeypot) == 1
    assert honeypot[0].importance == Importance.HIGH
    assert "honeypot" in honeypot[0].summary.lower()


def test_high_risk_insight() -> None:
    signals = RiskSignals(
        risk_score=0.9,
        risk_indicators=["Hidden Owner", "Mintable Token"],
        is_honeypot="0",
    )
    insights = generate_insights(ENTITY_ID, signals)
    high_risk = [i for i in insights if i.insight_type == "HighRiskDetected"]
    assert len(high_risk) == 1
    assert high_risk[0].importance == Importance.HIGH


def test_moderate_risk_insight() -> None:
    signals = RiskSignals(
        risk_score=0.6,
        risk_indicators=["Upgradeable Proxy"],
        is_honeypot="0",
    )
    insights = generate_insights(ENTITY_ID, signals)
    moderate = [i for i in insights if i.insight_type == "ModerateRiskDetected"]
    assert len(moderate) == 1
    assert moderate[0].importance == Importance.MEDIUM


def test_high_sell_tax_insight() -> None:
    signals = RiskSignals(sell_tax="0.25", risk_score=0.0)
    insights = generate_insights(ENTITY_ID, signals)
    tax_insights = [i for i in insights if i.insight_type == "HighSellTax"]
    assert len(tax_insights) == 1
    assert tax_insights[0].importance == Importance.MEDIUM
    assert "25%" in tax_insights[0].summary


def test_blacklist_insight() -> None:
    signals = RiskSignals(is_blacklisted="1", risk_score=0.0)
    insights = generate_insights(ENTITY_ID, signals)
    bl = [i for i in insights if i.insight_type == "BlacklistFunction"]
    assert len(bl) == 1
    assert bl[0].importance == Importance.MEDIUM


def test_airdrop_scam_insight() -> None:
    signals = RiskSignals(is_airdrop_scam="1", risk_score=0.0)
    insights = generate_insights(ENTITY_ID, signals)
    scam = [i for i in insights if i.insight_type == "AirdropScam"]
    assert len(scam) == 1
    assert scam[0].importance == Importance.HIGH


def test_fake_token_insight() -> None:
    signals = RiskSignals(fake_token="1", risk_score=0.0)
    insights = generate_insights(ENTITY_ID, signals)
    fake = [i for i in insights if i.insight_type == "FakeToken"]
    assert len(fake) == 1
    assert fake[0].importance == Importance.HIGH


def test_clean_token_no_insights() -> None:
    signals = RiskSignals(risk_score=0.0)
    insights = generate_insights(ENTITY_ID, signals)
    assert len(insights) == 0


def test_insight_deterministic() -> None:
    """Same inputs → same Insights, 100 iterations."""
    signals = RiskSignals(
        is_honeypot="0",
        hidden_owner="1",
        sell_tax="0.3",
        risk_score=0.7,
        risk_indicators=["Hidden Owner", "High Sell Tax (30%)"],
    )
    results = []
    for _ in range(100):
        insights = generate_insights(ENTITY_ID, signals)
        results.append(tuple(i.insight_type for i in insights))
    assert len(set(results)) == 1  # all identical


def test_insight_source_features_empty() -> None:
    """Insights from risk signals have empty source_features (no Features
    used — DOC-012 § Traceability Chain)."""
    signals = RiskSignals(is_honeypot="1", risk_score=1.0)
    insights = generate_insights(ENTITY_ID, signals)
    for insight in insights:
        assert insight.source_features == []
