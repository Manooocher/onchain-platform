"""Outcome Engine — produces ground-truth labels (DOC-012 § B.4, DOC-008 §
Outcome).

Given an entity whose observation window has closed, the engine gathers the
PIT-correct input data (Observation Snapshots + Market Bars available at or
before evaluation_timestamp), reads the honeypot flag from the persisted
insights table, and applies the versioned deterministic rules in
analytics/outcome_rules.py.

Determinism (DOC-013 § Determinism Discipline): NO wall-clock inside this
module — `clock` is injected, and `evaluation_timestamp` is the caller-
supplied, deterministic "creation_time + window", never now(). No set
iteration, no unseeded randomness.

Import-linter: analytics/ must NOT import intelligence/ (DOC-011). The
honeypot signal is a caller-resolved plain bool, read from the persisted
insights table via persistence/ only.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.analytics import outcome_rules
from onchain_platform.domain.schemas.enums import BarInterval, OutcomeType
from onchain_platform.domain.schemas.outcome import Outcome
from onchain_platform.persistence.postgres.outcomes_insights import get_latest_insight
from onchain_platform.persistence.timescale import repositories as ts_repos

logger = structlog.get_logger(__name__)

# INSIGHT_TYPE_HONEYPOT matches the insight_type emitted by
# intelligence/insight_generator.py (Rule 1: "HoneypotDetected").
INSIGHT_TYPE_HONEYPOT = "HoneypotDetected"

# list_snapshots / list_bars use an exclusive upper bound; add a small
# epsilon so a snapshot/bar whose timestamp equals evaluation_timestamp is
# included (PIT semantics: data available at or before as_of).
_PIT_EPSILON_SECONDS = 1


def parse_observation_window(window: str) -> int:
    """Parse an observation_window string into a duration in seconds.

    Supported: "1h", "24h", "7d". Deterministic, no wall-clock.
    Raises ValueError on an unsupported unit.
    """
    unit = window[-1]
    value = int(window[:-1])
    factors = {"m": 60, "h": 3600, "d": 86400}
    if unit not in factors:
        raise ValueError(f"unsupported observation_window unit in {window!r}")
    return value * factors[unit]


async def _is_honeypot(session: AsyncSession, entity_id: str) -> bool:
    """True if a persisted 'HoneypotDetected' insight exists for the entity.

    Reads the insights table (M7's persisted artifact) — NOT transient
    RiskSignals, and NOT from/out of analytics/ (DOC-011). If the pair was
    never scanned there is no insight, so the honeypot rule simply doesn't
    fire (the reserve-collapse rule still covers most rugs).
    """
    insight = await get_latest_insight(session, entity_id, INSIGHT_TYPE_HONEYPOT)
    if insight is not None:
        logger.debug("outcome_honeypot_detected", entity_id=entity_id)
    return insight is not None


async def evaluate_outcome(
    session: AsyncSession,
    *,
    entity_id: str,
    outcome_type: OutcomeType,
    observation_window: str,
    evaluation_timestamp: datetime,
    clock: Callable[[], datetime],
) -> Outcome | None:
    """Evaluate one outcome type for an entity whose window has closed.

    Returns an Outcome with the deterministic label_value, or None if the
    window contains no data at all (caller treats as 'not evaluable').

    All input queries are PIT-filtered to `<= evaluation_timestamp` — never
    uses post-close data (DOC-012 § B.3, DOC-008 § Point-in-Time).
    """
    window_seconds = parse_observation_window(observation_window)
    from_time = evaluation_timestamp - timedelta(seconds=window_seconds)
    to_time = evaluation_timestamp + timedelta(seconds=_PIT_EPSILON_SECONDS)

    # PIT-correct inputs, deterministically ordered by the repository.
    snapshots = list(await ts_repos.list_snapshots(session, entity_id, from_time, to_time))
    bars = list(
        await ts_repos.list_bars(
            session,
            entity_id,
            BarInterval.ONE_MINUTE,
            from_time,
            to_time,
        )
    )

    if not snapshots and not bars:
        logger.info(
            "outcome_insufficient_data",
            entity_id=entity_id,
            outcome_type=outcome_type.value,
        )
        return None

    is_honeypot = await _is_honeypot(session, entity_id)
    label_value = outcome_rules.evaluate_for_type(
        outcome_type.value, snapshots, bars, is_honeypot, observation_window
    )

    outcome = Outcome.create(
        entity_id=entity_id,
        outcome_type=outcome_type,
        observation_window=observation_window,
        label_definition=outcome_rules.label_definition_for(outcome_type.value, observation_window),
        label_definition_version=outcome_rules.OUTCOME_RULES_VERSION,
        evaluation_timestamp=evaluation_timestamp,
        evaluated_at=clock(),
        label_value=label_value,
    )

    logger.info(
        "outcome_evaluated",
        entity_id=entity_id,
        outcome_type=outcome_type.value,
        label_value=label_value,
        framework_version=outcome_rules.OUTCOME_RULES_VERSION,
        snapshot_count=len(snapshots),
        bar_count=len(bars),
    )
    return outcome
