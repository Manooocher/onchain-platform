"""Outcome evaluation job — APScheduler callback runner (Milestone 8).

Scans all FINALIZED trading pairs, evaluates any whose observation window
has closed, and persists the labels. Deterministic and idempotent:
- Only pairs whose creating PAIR_CREATED fact is FINALIZED are eligible
  ("Finality Before Analytics", ADR-006).
- A pair is evaluated once — if an outcome already exists for that
  (entity_id, outcome_type) it is skipped (one-shot, D8).
- persists via save_outcome (ON CONFLICT DO NOTHING — belt-and-braces
  idempotency).

Determinism (DOC-013): the clock is injected (wall-clock only in main.py);
evaluation_timestamp is deterministic (creation_time + window), never now().
"""

from collections.abc import Callable
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics import outcome_engine
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.schemas.enums import OutcomeType
from onchain_platform.persistence.postgres import entity_repositories, repositories
from onchain_platform.persistence.postgres.outcomes_insights import (
    get_latest_outcome,
    save_outcome,
)

logger = structlog.get_logger(__name__)

# The active observation windows for the MVP (ships "1h"; "24h"/"7d" are
# supported by the parser and can be enabled as live cohort data accrues).
ACTIVE_OBSERVATION_WINDOWS = ("1h",)

_OUTCOME_TYPES = (
    OutcomeType.RUG_PULL,
    OutcomeType.SUCCESSFUL_LAUNCH,
    OutcomeType.DEAD_TOKEN,
)


async def _pair_creation_time(session: AsyncSession, pair: TradingPair) -> datetime | None:
    """event_time of the pair's PAIR_CREATED fact (from creation_fact_id).

    deterministic source of the pair's birth time — outcome evaluation
    timestamps are derived from this, not from the wall clock.
    """
    fact = await repositories.get_fact(session, pair.creation_fact_id)
    if fact is None:
        return None
    return fact.event_time


async def run_outcome_evaluation(
    pg_engine: AsyncEngine,
    clock: Callable[[], datetime],
) -> tuple[int, int, int]:
    """Evaluate every eligible pair whose observation window has closed.

    Returns (pairs_evaluated, outcomes_created, pairs_rechecked) where
    pairs_rechecked counts pairs seen but already-labelled (skipped).
    """
    pairs_evaluated = 0
    outcomes_created = 0
    pairs_rechecked = 0
    now = clock()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        eligible_pairs = await entity_repositories.list_all_trading_pairs(session)
        for pair in eligible_pairs:
            creation_time = await _pair_creation_time(session, pair)
            if creation_time is None:
                logger.warning(
                    "outcome_skip_no_creation_fact",
                    entity_id=pair.canonical_id,
                    creation_fact_id=pair.creation_fact_id,
                )
                continue

            for window in ACTIVE_OBSERVATION_WINDOWS:
                try:
                    window_seconds = outcome_engine.parse_observation_window(window)
                except ValueError as exc:  # pragma: no cover — static config
                    logger.error("outcome_bad_window", window=window, error=str(exc))
                    continue

                window_elapsed = now - creation_time
                if window_elapsed < timedelta(seconds=window_seconds):
                    continue  # window not yet closed

                evaluation_timestamp = creation_time + timedelta(seconds=window_seconds)

                for outcome_type in _OUTCOME_TYPES:
                    # One-shot: skip if already evaluated for this type.
                    existing = await get_latest_outcome(session, pair.canonical_id, outcome_type)
                    if existing is not None:
                        pairs_rechecked += 1
                        continue

                    outcome = await outcome_engine.evaluate_outcome(
                        session,
                        entity_id=pair.canonical_id,
                        outcome_type=outcome_type,
                        observation_window=window,
                        evaluation_timestamp=evaluation_timestamp,
                        clock=clock,
                    )
                    if outcome is None:
                        continue  # not enough data — retry on a later run
                    created = await save_outcome(session, outcome)
                    if created:
                        outcomes_created += 1

                pairs_evaluated += 1

    logger.info(
        "outcome_job_complete",
        pairs_evaluated=pairs_evaluated,
        outcomes_created=outcomes_created,
        pairs_rechecked=pairs_rechecked,
        window=ACTIVE_OBSERVATION_WINDOWS,
    )
    return pairs_evaluated, outcomes_created, pairs_rechecked
