"""Insight Generator — converts risk signals + features into Insights
(DOC-012 § B.4, DOC-008 § Insights).

DOC-008: "Insights summarize Features." An Insight never becomes input to
a downstream pipeline.

Deterministic (DOC-013 § Determinism Discipline): same inputs → same
Insights. No wall-clock, no set iteration, deterministic rule ordering.
"""

from datetime import UTC, datetime

import structlog

from onchain_platform.domain.schemas.enums import Importance
from onchain_platform.domain.schemas.insight import Insight
from onchain_platform.domain.schemas.risk_signals import RiskSignals

logger = structlog.get_logger(__name__)


def generate_insights(
    entity_id: str,
    risk_signals: RiskSignals,
    generated_at: datetime | None = None,
) -> list[Insight]:
    """Generate Insights from risk signals.

    Deterministic: same risk_signals → same Insights, always.
    Rule ordering is fixed (not dependent on dict iteration order).
    """
    if generated_at is None:
        generated_at = datetime.now(UTC)

    insights: list[Insight] = []

    # Rule 1: Honeypot → HIGH importance.
    if risk_signals.is_honeypot == "1":
        insights.append(
            Insight(
                insight_id=f"{entity_id}|HoneypotDetected|{generated_at.isoformat()}",
                entity_id=entity_id,
                insight_type="HoneypotDetected",
                summary="This token appears to be a honeypot — cannot sell.",
                generated_at=generated_at,
                source_features=[],
                importance=Importance.HIGH,
            )
        )

    # Rule 2: High risk score → HIGH importance.
    if risk_signals.risk_score >= 0.8 and risk_signals.is_honeypot != "1":
        indicators_str = ", ".join(risk_signals.risk_indicators[:3])
        insights.append(
            Insight(
                insight_id=f"{entity_id}|HighRiskDetected|{generated_at.isoformat()}",
                entity_id=entity_id,
                insight_type="HighRiskDetected",
                summary=(
                    f"High risk score ({risk_signals.risk_score:.2f}). "
                    f"Key concerns: {indicators_str}."
                ),
                generated_at=generated_at,
                source_features=[],
                importance=Importance.HIGH,
            )
        )

    # Rule 3: Moderate risk score → MEDIUM importance.
    elif risk_signals.risk_score >= 0.5:
        indicators_str = ", ".join(risk_signals.risk_indicators[:3])
        insights.append(
            Insight(
                insight_id=f"{entity_id}|ModerateRiskDetected|{generated_at.isoformat()}",
                entity_id=entity_id,
                insight_type="ModerateRiskDetected",
                summary=(
                    f"Moderate risk score ({risk_signals.risk_score:.2f}). "
                    f"Key concerns: {indicators_str}."
                ),
                generated_at=generated_at,
                source_features=[],
                importance=Importance.MEDIUM,
            )
        )

    # Rule 4: High sell tax → MEDIUM importance.
    sell_tax = _parse_tax(risk_signals.sell_tax)
    if sell_tax > 0.2:
        insights.append(
            Insight(
                insight_id=f"{entity_id}|HighSellTax|{generated_at.isoformat()}",
                entity_id=entity_id,
                insight_type="HighSellTax",
                summary=f"High sell tax detected ({sell_tax:.0%}). This may indicate a tax token.",
                generated_at=generated_at,
                source_features=[],
                importance=Importance.MEDIUM,
            )
        )

    # Rule 5: Blacklist function → MEDIUM importance.
    if risk_signals.is_blacklisted == "1":
        insights.append(
            Insight(
                insight_id=f"{entity_id}|BlacklistFunction|{generated_at.isoformat()}",
                entity_id=entity_id,
                insight_type="BlacklistFunction",
                summary=(
                    "Contract has a blacklist function — addresses can be blocked from trading."
                ),
                generated_at=generated_at,
                source_features=[],
                importance=Importance.MEDIUM,
            )
        )

    # Rule 6: Airdrop scam → HIGH importance.
    if risk_signals.is_airdrop_scam == "1":
        insights.append(
            Insight(
                insight_id=f"{entity_id}|AirdropScam|{generated_at.isoformat()}",
                entity_id=entity_id,
                insight_type="AirdropScam",
                summary="This token has been flagged as an airdrop scam.",
                generated_at=generated_at,
                source_features=[],
                importance=Importance.HIGH,
            )
        )

    # Rule 7: Fake token → HIGH importance.
    if risk_signals.fake_token == "1":
        insights.append(
            Insight(
                insight_id=f"{entity_id}|FakeToken|{generated_at.isoformat()}",
                entity_id=entity_id,
                insight_type="FakeToken",
                summary="This token has been flagged as a fake/counterfeit token.",
                generated_at=generated_at,
                source_features=[],
                importance=Importance.HIGH,
            )
        )

    logger.info(
        "insights_generated",
        entity_id=entity_id,
        insight_count=len(insights),
        risk_score=risk_signals.risk_score,
    )
    return insights


def _parse_tax(val: object) -> float:
    if val is None:
        return 0.0
    try:
        return float(str(val))
    except (ValueError, TypeError):
        return 0.0
