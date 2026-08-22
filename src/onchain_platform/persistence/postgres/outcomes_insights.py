"""Insight ORM model + CRUD (DOC-012 § B.4, DOC-014 § Storage Assignment).

DOC-014 § Storage Assignment: "Outcome, Insight (Part B.4) → PostgreSQL →
outcomes, insights — regular tables, not hypertables."

DOC-008: "Insights summarize Features." An Insight never becomes input to
a downstream pipeline.
"""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import DateTime, Enum, Index, Text, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from onchain_platform.domain.exceptions import PersistenceError
from onchain_platform.domain.schemas.enums import Importance
from onchain_platform.domain.schemas.insight import Insight


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
