"""Insight ORM model + CRUD (DOC-012 § B.4, DOC-014 § Storage Assignment).

DOC-014 § Storage Assignment: "Outcome, Insight (Part B.4) → PostgreSQL →
outcomes, insights — regular tables, not hypertables."

DOC-008: "Insights summarize Features." An Insight never becomes input to
a downstream pipeline.
"""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, Text, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from onchain_platform.domain.exceptions import PersistenceError
from onchain_platform.domain.schemas.enums import Importance, OutcomeType
from onchain_platform.domain.schemas.insight import Insight
from onchain_platform.domain.schemas.outcome import Outcome


class InsightBase(DeclarativeBase):
    """Declarative base for Insight ORM model."""


class InsightRow(InsightBase):
    """Insight table (DOC-012 § B.4, DOC-014 § Storage Assignment).

    Regular PostgreSQL table, not a hypertable (DOC-014 § Storage
    Assignment: "Outcome, Insight (Part B.4) → PostgreSQL → outcomes,
    insights — regular tables, not hypertables").
    """

    __tablename__ = "insights"

    insight_id: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    insight_type: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_features: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="ARRAY[]::TEXT[]"
    )
    importance: Mapped[Importance] = mapped_column(
        Enum(Importance, name="importance_enum", native_enum=True),
        nullable=False,
    )

    __table_args__ = (
        # DOC-014 § Indexing Strategy: "Research querying 'what happened
        # to this entity' without needing every historical outcome scanned."
        Index("ix_insights_entity_time", "entity_id", "generated_at"),
    )


def _insight_to_row_values(ins: Insight) -> dict[str, object]:
    return {
        "insight_id": ins.insight_id,
        "schema_version": ins.schema_version,
        "entity_id": ins.entity_id,
        "insight_type": ins.insight_type,
        "summary": ins.summary,
        "generated_at": ins.generated_at,
        "source_features": ins.source_features,
        "importance": ins.importance,
    }


def _row_to_insight(row: InsightRow) -> Insight:
    return Insight(
        insight_id=row.insight_id,
        entity_id=row.entity_id,
        insight_type=row.insight_type,
        summary=row.summary,
        generated_at=_ensure_utc(row.generated_at),
        source_features=row.source_features,
        importance=row.importance,
    )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def save_insight(session: AsyncSession, insight: Insight) -> bool:
    """Upsert an Insight (INSERT ON CONFLICT UPDATE on insight_id)."""
    stmt = (
        pg_insert(InsightRow)
        .values(**_insight_to_row_values(insight))
        .on_conflict_do_update(
            index_elements=["insight_id"],
            set_={k: v for k, v in _insight_to_row_values(insight).items() if k != "insight_id"},
        )
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save Insight {insight.insight_id}") from exc
    return bool(result.rowcount == 1)


async def list_insights_for_entity(session: AsyncSession, entity_id: str) -> list[Insight]:
    """List Insights for an entity, ordered by generated_at DESC."""
    stmt = (
        select(InsightRow)
        .where(InsightRow.entity_id == entity_id)
        .order_by(InsightRow.generated_at.desc())
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list Insights for {entity_id}") from exc
    return [_row_to_insight(row) for row in rows]


async def get_latest_insight(
    session: AsyncSession, entity_id: str, insight_type: str
) -> Insight | None:
    """Most recent Insight of a given type for an entity."""
    stmt = (
        select(InsightRow)
        .where(
            InsightRow.entity_id == entity_id,
            InsightRow.insight_type == insight_type,
        )
        .order_by(InsightRow.generated_at.desc())
        .limit(1)
    )
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(
            f"failed to read latest Insight {insight_type} for {entity_id}"
        ) from exc
    return _row_to_insight(row) if row is not None else None


async def get_latest_insight_as_of(
    session: AsyncSession,
    entity_id: str,
    insight_type: str | None,
    as_of: datetime,
) -> Insight | None:
    """Most recent Insight (optionally of a given type) with generated_at <= as_of.

    Point-in-Time variant of `get_latest_insight`: filters on
    `generated_at <= as_of` so a Feature computed with this insight respects
    PIT correctness (DOC-008 § D: never use future data). When `insight_type`
    is None, returns the entity's most recent Insight of any type as of as_of.
    """
    stmt = select(InsightRow).where(
        InsightRow.entity_id == entity_id,
        InsightRow.generated_at <= as_of,
    )
    if insight_type is not None:
        stmt = stmt.where(InsightRow.insight_type == insight_type)
    stmt = stmt.order_by(InsightRow.generated_at.desc()).limit(1)
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(
            f"failed to read latest Insight {insight_type or 'any'} for {entity_id} as of {as_of}"
        ) from exc
    return _row_to_insight(row) if row is not None else None


# ---------------------------------------------------------------------------
# Outcome (DOC-012 § B.4, DOC-014 § Storage Assignment)
# ---------------------------------------------------------------------------


class OutcomeBase(DeclarativeBase):
    """Declarative base for the Outcome ORM model."""


class OutcomeRow(OutcomeBase):
    """Outcome table (DOC-012 § B.4, DOC-014 § Storage Assignment).

    Regular PostgreSQL table, not a hypertable (DOC-014 § Storage
    Assignment: "Outcome, Insight (§ B.4) → PostgreSQL → outcomes,
    insights — regular tables, not hypertables").

    Outcomes are ground-truth labels; a row is immutable once written
    (DOC-013 § Immutability). The idempotent upsert is ON CONFLICT DO
    NOTHING — re-evaluation never overwrites a historical label.
    """

    __tablename__ = "outcomes"

    outcome_id: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_type: Mapped[OutcomeType] = mapped_column(
        Enum(OutcomeType, name="outcome_type_enum", native_enum=True),
        nullable=False,
    )
    observation_window: Mapped[str] = mapped_column(Text, nullable=False)
    label_definition: Mapped[str] = mapped_column(Text, nullable=False)
    label_definition_version: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label_value: Mapped[bool] = mapped_column(nullable=False)

    __table_args__ = (
        # DOC-014 § Data Integrity Constraints: label_value IS NOT NULL on a
        # finalized Outcome row.
        CheckConstraint("label_value IS NOT NULL", name="ck_outcome_label_value_not_null"),
        # DOC-014 § Indexing Strategy: research querying "what happened to
        # this entity" without needing every historical outcome scanned.
        Index(
            "ix_outcomes_entity_type_time",
            "entity_id",
            "outcome_type",
            "evaluation_timestamp",
            postgresql_using="btree",
        ),
    )


def _outcome_to_row_values(outcome: Outcome) -> dict[str, object]:
    return {
        "outcome_id": outcome.outcome_id,
        "schema_version": outcome.schema_version,
        "entity_id": outcome.entity_id,
        "outcome_type": outcome.outcome_type,
        "observation_window": outcome.observation_window,
        "label_definition": outcome.label_definition,
        "label_definition_version": outcome.label_definition_version,
        "evaluation_timestamp": outcome.evaluation_timestamp,
        "evaluated_at": outcome.evaluated_at,
        "label_value": outcome.label_value,
    }


def _row_to_outcome(row: OutcomeRow) -> Outcome:
    return Outcome(
        outcome_id=row.outcome_id,
        entity_id=row.entity_id,
        outcome_type=row.outcome_type,
        observation_window=row.observation_window,
        label_definition=row.label_definition,
        label_definition_version=row.label_definition_version,
        evaluation_timestamp=_ensure_utc(row.evaluation_timestamp),
        evaluated_at=_ensure_utc(row.evaluated_at),
        label_value=row.label_value,
    )


async def save_outcome(session: AsyncSession, outcome: Outcome) -> bool:
    """Upsert an Outcome. Returns True if a new row was inserted, False if a
    row with this outcome_id already existed (idempotent).

    ON CONFLICT DO NOTHING (DOC-008/ADR-006 Idempotency) — re-evaluation
    never overwrites a historical label; outcome_id is the deterministic
    natural key (entity_id|outcome_type|evaluation_timestamp).
    """
    stmt = (
        pg_insert(OutcomeRow)
        .values(**_outcome_to_row_values(outcome))
        .on_conflict_do_nothing(index_elements=["outcome_id"])
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save Outcome {outcome.outcome_id}") from exc
    return bool(result.rowcount == 1)


async def list_outcomes_for_entity(
    session: AsyncSession, entity_id: str, outcome_type: OutcomeType | None = None
) -> list[Outcome]:
    """List Outcomes for an entity, ordered by evaluation_timestamp DESC.

    DOC-014 § Indexing Strategy: the (entity_id, outcome_type,
    evaluation_timestamp) index serves "what happened to this entity".
    """
    stmt = select(OutcomeRow).where(OutcomeRow.entity_id == entity_id)
    if outcome_type is not None:
        stmt = stmt.where(OutcomeRow.outcome_type == outcome_type)
    stmt = stmt.order_by(OutcomeRow.evaluation_timestamp.desc())
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list Outcomes for {entity_id}") from exc
    return [_row_to_outcome(row) for row in rows]


async def list_outcomes_range(
    session: AsyncSession,
    entity_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Outcome]:
    """Outcomes for an entity within a time range (dataset assembly).

    Filters on evaluation_timestamp within [start, end], ordered ascending
    (DOC-015 § The Research Dataset Assembly — range bounded, not paged).
    """
    stmt = select(OutcomeRow).where(OutcomeRow.entity_id == entity_id)
    if start is not None:
        stmt = stmt.where(OutcomeRow.evaluation_timestamp >= start)
    if end is not None:
        stmt = stmt.where(OutcomeRow.evaluation_timestamp <= end)
    stmt = stmt.order_by(OutcomeRow.evaluation_timestamp.asc())
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list Outcomes for {entity_id} in range") from exc
    return [_row_to_outcome(row) for row in rows]


async def get_latest_outcome(
    session: AsyncSession, entity_id: str, outcome_type: OutcomeType
) -> Outcome | None:
    """Most recent Outcome of a given type for an entity."""
    stmt = (
        select(OutcomeRow)
        .where(
            OutcomeRow.entity_id == entity_id,
            OutcomeRow.outcome_type == outcome_type,
        )
        .order_by(OutcomeRow.evaluation_timestamp.desc())
        .limit(1)
    )
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(
            f"failed to read latest Outcome {outcome_type} for {entity_id}"
        ) from exc
    return _row_to_outcome(row) if row is not None else None


async def list_outcomes_page(
    session: AsyncSession,
    entity_id: str,
    *,
    outcome_type: OutcomeType | None = None,
    cursor: dict[str, object] | None = None,
    limit: int = 100,
) -> tuple[list[Outcome], dict[str, object] | None]:
    """Paged Outcomes for an entity, cursor on evaluation_timestamp DESC.

    Serves `GET /v1/entities/{id}/outcomes` (DOC-015). Keyset pagination over
    `evaluation_timestamp` (descending — newest first). Returns
    (items, next_cursor_keys).
    """
    stmt = select(OutcomeRow).where(OutcomeRow.entity_id == entity_id)
    if outcome_type is not None:
        stmt = stmt.where(OutcomeRow.outcome_type == outcome_type)
    if cursor is not None:
        last_ts = datetime.fromisoformat(str(cursor["evaluation_timestamp"]))
        stmt = stmt.where(OutcomeRow.evaluation_timestamp < last_ts)
    stmt = stmt.order_by(OutcomeRow.evaluation_timestamp.desc()).limit(limit + 1)
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list Outcomes for {entity_id}") from exc

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = cast(
        "dict[str, object] | None",
        (
            {"evaluation_timestamp": page[-1].evaluation_timestamp.isoformat()}
            if has_more and page
            else None
        ),
    )
    return [_row_to_outcome(r) for r in page], next_cursor


async def list_insights_page(
    session: AsyncSession,
    entity_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    insight_type: str | None = None,
    cursor: dict[str, object] | None = None,
    limit: int = 100,
) -> tuple[list[Insight], dict[str, object] | None]:
    """Paged Insights for an entity, cursor on generated_at DESC.

    Serves `GET /v1/entities/{id}/insights` (DOC-015). Returns
    (items, next_cursor_keys).
    """
    stmt = select(InsightRow).where(InsightRow.entity_id == entity_id)
    if start is not None:
        stmt = stmt.where(InsightRow.generated_at >= start)
    if end is not None:
        stmt = stmt.where(InsightRow.generated_at <= end)
    if insight_type is not None:
        stmt = stmt.where(InsightRow.insight_type == insight_type)
    if cursor is not None:
        last_ts = datetime.fromisoformat(str(cursor["generated_at"]))
        stmt = stmt.where(InsightRow.generated_at < last_ts)
    stmt = stmt.order_by(InsightRow.generated_at.desc()).limit(limit + 1)
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list Insights for {entity_id}") from exc

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = cast(
        "dict[str, object] | None",
        ({"generated_at": page[-1].generated_at.isoformat()} if has_more and page else None),
    )
    return [_row_to_insight(r) for r in page], next_cursor
