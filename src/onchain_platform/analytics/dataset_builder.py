"""Dataset builder — joins features to outcomes for ML training, with a
ZERO-LEAKAGE guarantee (Issue 2: Data Leakage in Feature-Outcome Join).

The ML dataset is built from `features` (Timescale hypertable) and `outcomes`
(Postgres). To train a Rug-Pull / Liquidity / Momentum model on this data the
features used to explain an outcome MUST have been knowable at the moment the
outcome was evaluated — i.e. every feature's `as_of_timestamp` must be
**<= the outcome's `evaluation_timestamp`**.

Leakage happens when a feature computed *after* the outcome is used to explain
it (e.g. a feature with `as_of` 308h after the evaluation would leak future
information into the training row). This module guarantees, by construction
and by an explicit validation assertion, that no training row leaks.

How it guarantees zero leakage:
1. For each outcome, it fetches the pair's **latest-per-name features as of
   `outcome.evaluation_timestamp`** via `ts_repos.list_latest_features(...,
   as_of=evaluation_timestamp)` — that reader only returns features whose
   `as_of_timestamp <= evaluation_timestamp` (PIT semantics, DOC-008 § D).
2. It records each feature's `as_of_timestamp` on the row, and
   `assert_zero_leakage` re-checks the invariant `as_of <= evaluation` before
   returning, so a future join bug is caught in CI rather than in a trained
   model.

Lives in `analytics/` (may import `persistence/` + `domain/` per DOC-011; the
feature/outcome repos it reads are both allowed). Deterministic: ordered
iteration, no set-aggregation, no wall-clock.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal  # noqa: F401  (kept for the monetary contract note)

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.enums import OutcomeType
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.domain.schemas.outcome import Outcome
from onchain_platform.persistence.postgres import entity_repositories
from onchain_platform.persistence.postgres import (
    outcomes_insights as outcomes_repo,
)
from onchain_platform.persistence.timescale import repositories as ts_repos

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FeatureSnapshot:
    """One feature value at its own as_of timestamp (kept for PIT audit)."""

    feature_name: str
    value: float
    as_of_timestamp: datetime


@dataclass(frozen=True)
class TrainingRow:
    """One row of the training dataset: an outcome + the features that were
    knowable at/before its evaluation (no lookahead)."""

    entity_id: str
    outcome_type: OutcomeType
    observation_window: str
    label_value: bool
    evaluation_timestamp: datetime
    # feature_name -> PIT feature (as_of <= evaluation_timestamp, by construction)
    features: dict[str, FeatureSnapshot] = field(default_factory=dict)

    def to_feature_vector(self) -> dict[str, float]:
        """Return {feature_name: value} for ML input (Feature.value is float)."""
        return {name: snap.value for name, snap in self.features.items()}


def assert_zero_leakage(rows: list[TrainingRow]) -> None:
    """Assert every feature in every row has as_of <= outcome evaluation
    (Issue 2 validation). Raises AssertionError listing offenders if violated.
    """
    violations: list[tuple[str, str, object, object]] = []
    for row in rows:
        for fname, snap in row.features.items():
            if snap.as_of_timestamp > row.evaluation_timestamp:
                violations.append(
                    (
                        row.entity_id,
                        fname,
                        snap.as_of_timestamp.isoformat(),
                        row.evaluation_timestamp.isoformat(),
                    )
                )
    if violations:
        raise AssertionError(
            f"dataset leakage: {len(violations)} feature(s) have as_of > evaluation "
            f"(last 5: {violations[-5:]})"
        )


def leakage_count(rows: list[TrainingRow]) -> int:
    """Number of (row, feature) pairs that violate the PIT constraint."""
    n = 0
    for row in rows:
        for snap in row.features.values():
            if snap.as_of_timestamp > row.evaluation_timestamp:
                n += 1
    return n


async def _features_as_of(
    session: AsyncSession, entity_id: str, as_of: datetime
) -> dict[str, FeatureSnapshot]:
    """Latest-per-name features for an entity at/before `as_of` (PIT).

    `ts_repos.list_latest_features` returns the most recent Feature per
    feature_name with as_of_timestamp <= `as_of` — the zero-leakage primitive.
    """
    latest: list[Feature] = await ts_repos.list_latest_features(session, entity_id, as_of)
    return {
        f.feature_name: FeatureSnapshot(
            feature_name=f.feature_name,
            value=f.value,
            as_of_timestamp=f.as_of_timestamp,
        )
        for f in latest
    }


async def build_training_dataset(
    session: AsyncSession,
    *,
    chain_id: int,
    outcome_type: OutcomeType,
    observation_window: str,
    max_rows: int | None = None,
) -> list[TrainingRow]:
    """Build the zero-leakage training dataset for one outcome type + window.

    For every trading pair, for every outcome of `outcome_type` in
    `observation_window`, attach the pair's latest-per-name features as of the
    outcome's evaluation timestamp. The dataset is ordered by
    (entity_id, evaluation_timestamp) for determinism.

    Validates zero leakage before returning (raises if the invariant broke).
    """
    rows: list[TrainingRow] = []
    pairs, _ = await entity_repositories.list_pairs(session, chain_id=chain_id)
    for pair in pairs:
        entity_id = pair.canonical_id
        outcomes: list[Outcome] = await outcomes_repo.list_outcomes_for_entity(
            session, entity_id, outcome_type=outcome_type
        )
        for outcome in outcomes:
            if outcome.observation_window != observation_window:
                continue
            features = await _features_as_of(session, entity_id, outcome.evaluation_timestamp)
            rows.append(
                TrainingRow(
                    entity_id=entity_id,
                    outcome_type=outcome.outcome_type,
                    observation_window=outcome.observation_window,
                    label_value=outcome.label_value,
                    evaluation_timestamp=outcome.evaluation_timestamp,
                    features=features,
                )
            )
            if max_rows is not None and len(rows) >= max_rows:
                break
        if max_rows is not None and len(rows) >= max_rows:
            break

    # Deterministic order (DOC-013): (entity_id, evaluation_timestamp).
    rows.sort(key=lambda r: (r.entity_id, r.evaluation_timestamp))

    assert_zero_leakage(rows)
    logger.info(
        "dataset_built",
        chain_id=chain_id,
        outcome_type=outcome_type.value,
        observation_window=observation_window,
        rows=len(rows),
        leakage=leakage_count(rows),
    )
    return rows


# Convenience type alias for tests / callers that need a clock injection.
ClockCallable = Callable[[], datetime]
