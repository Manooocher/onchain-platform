"""TimescaleDB repositories — Market Bars (DOC-012 § B.3, DOC-014 §
Storage Assignment & § TimescaleDB Hypertables).

market_bars is the first TimescaleDB hypertable. Partitioned by
bar_start_time (7-day chunks), compressed after 30 days (DOC-014 §
TimescaleDB Hypertables). Index: (pair_id, interval, bar_start_time DESC)
— the primary research query pattern (DOC-014 § Indexing Strategy).

All OHLCV columns are NUMERIC (unconstrained precision — DOC-014 § Type
Mapping Rules, "Price / Ratio / Derived-USD" category). trade_count is
INTEGER. source_fact_range stored as two TEXT columns (DOC-014 § Standard
mappings — tuple stored as two columns).
"""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    Text,
    distinct,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from onchain_platform.domain.exceptions import PersistenceError
from onchain_platform.domain.schemas.enums import BarInterval
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot


class TimescaleBase(DeclarativeBase):
    """Declarative base for TimescaleDB ORM models."""


class MarketBarRow(TimescaleBase):
    """Market Bar hypertable (DOC-012 § B.3, DOC-014 § TimescaleDB
    Hypertables).

    Partitioned by bar_start_time (7-day chunks). Compression policy:
    compress chunks older than 30 days.
    """

    __tablename__ = "market_bars"

    bar_id: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    pair_id: Mapped[str] = mapped_column(Text, nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    interval: Mapped[str] = mapped_column(Text, nullable=False)
    # bar_start_time is part of the PK because TimescaleDB hypertables
    # require the partitioning column in any unique index.
    bar_start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bar_end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # OHLCV — NUMERIC (unconstrained precision, DOC-014 § Type Mapping
    # Rules). All Decimal-as-string in the domain schema.
    open: Mapped[str] = mapped_column(Numeric, nullable=False)
    high: Mapped[str] = mapped_column(Numeric, nullable=False)
    low: Mapped[str] = mapped_column(Numeric, nullable=False)
    close: Mapped[str] = mapped_column(Numeric, nullable=False)
    volume_base: Mapped[str] = mapped_column(Numeric, nullable=False)
    volume_quote: Mapped[str] = mapped_column(Numeric, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    vwap: Mapped[str] = mapped_column(Numeric, nullable=False)
    buy_volume: Mapped[str] = mapped_column(Numeric, nullable=False)
    sell_volume: Mapped[str] = mapped_column(Numeric, nullable=False)
    # source_fact_range stored as two columns (DOC-014 § Standard
    # mappings — tuple stored as two columns).
    source_fact_range_start: Mapped[str] = mapped_column(Text, nullable=False)
    source_fact_range_end: Mapped[str] = mapped_column(Text, nullable=False)
    is_provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # CHECK constraints for values Pydantic already validates but the
        # database can cheaply double-check (DOC-014 § Data Integrity
        # Constraints).
        CheckConstraint("volume_base >= 0", name="ck_volume_base_non_negative"),
        CheckConstraint("volume_quote >= 0", name="ck_volume_quote_non_negative"),
        CheckConstraint("buy_volume >= 0", name="ck_buy_volume_non_negative"),
        CheckConstraint("sell_volume >= 0", name="ck_sell_volume_non_negative"),
        CheckConstraint("trade_count >= 0", name="ck_trade_count_non_negative"),
        # Primary research query: OHLCV history for one pair, one interval,
        # over a time range (DOC-014 § Indexing Strategy).
        Index(
            "ix_market_bars_pair_interval_time",
            "pair_id",
            "interval",
            "bar_start_time",
            postgresql_using="btree",
        ),
    )


def _bar_to_row_values(bar: MarketBar) -> dict[str, object]:
    """Domain schema → column values."""
    return {
        "bar_id": bar.bar_id,
        "schema_version": bar.schema_version,
        "pair_id": bar.pair_id,
        "chain_id": bar.chain_id,
        "interval": bar.interval.value,
        "bar_start_time": bar.bar_start_time,
        "bar_end_time": bar.bar_end_time,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume_base": bar.volume_base,
        "volume_quote": bar.volume_quote,
        "trade_count": bar.trade_count,
        "vwap": bar.vwap,
        "buy_volume": bar.buy_volume,
        "sell_volume": bar.sell_volume,
        "source_fact_range_start": bar.source_fact_range[0],
        "source_fact_range_end": bar.source_fact_range[1],
        "is_provisional": bar.is_provisional,
        "computed_at": bar.computed_at,
    }


def _row_to_bar(row: MarketBarRow) -> MarketBar:
    """ORM row → domain schema."""
    return MarketBar(
        schema_version=row.schema_version,
        bar_id=row.bar_id,
        pair_id=row.pair_id,
        chain_id=row.chain_id,
        interval=BarInterval(row.interval),
        bar_start_time=_ensure_utc(row.bar_start_time),
        bar_end_time=_ensure_utc(row.bar_end_time),
        open=str(row.open),
        high=str(row.high),
        low=str(row.low),
        close=str(row.close),
        volume_base=str(row.volume_base),
        volume_quote=str(row.volume_quote),
        trade_count=row.trade_count,
        vwap=str(row.vwap),
        buy_volume=str(row.buy_volume),
        sell_volume=str(row.sell_volume),
        source_fact_range=(row.source_fact_range_start, row.source_fact_range_end),
        is_provisional=row.is_provisional,
        computed_at=_ensure_utc(row.computed_at),
    )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def save_bar(session: AsyncSession, bar: MarketBar) -> None:
    """Upsert a Market Bar (INSERT ON CONFLICT UPDATE for reorg
    recomputation — DOC-012 § B.3: 'the entire bar is recomputed from the
    predicate above — never patched incrementally')."""
    stmt = (
        pg_insert(MarketBarRow)
        .values(**_bar_to_row_values(bar))
        .on_conflict_do_update(
            index_elements=["bar_id", "bar_start_time"],
            set_={
                k: v
                for k, v in _bar_to_row_values(bar).items()
                if k not in ("bar_id", "bar_start_time")
            },
        )
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save MarketBar {bar.bar_id}") from exc


async def list_bars(
    session: AsyncSession,
    pair_id: str,
    interval: BarInterval,
    from_time: datetime,
    to_time: datetime,
) -> list[MarketBar]:
    """List Market Bars for a pair in a time range, ordered by
    bar_start_time (DOC-014 § Indexing Strategy)."""
    stmt = (
        select(MarketBarRow)
        .where(
            MarketBarRow.pair_id == pair_id,
            MarketBarRow.interval == interval,
            MarketBarRow.bar_start_time >= from_time,
            MarketBarRow.bar_start_time < to_time,
        )
        .order_by(MarketBarRow.bar_start_time)
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list MarketBars for {pair_id}") from exc
    return [_row_to_bar(row) for row in rows]


async def get_bar(session: AsyncSession, bar_id: str) -> MarketBar | None:
    """Read one Market Bar by its natural key."""
    stmt = select(MarketBarRow).where(MarketBarRow.bar_id == bar_id)
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to read MarketBar {bar_id}") from exc
    return _row_to_bar(row) if row is not None else None


# ---------------------------------------------------------------------------
# ObservationSnapshot (DOC-012 § B.3)
# ---------------------------------------------------------------------------


class ObservationSnapshotRow(TimescaleBase):
    """ObservationSnapshot hypertable (DOC-012 § B.3, DOC-014 § TimescaleDB
    Hypertables).

    Partitioned by snapshot_timestamp (1-day chunks). Compression policy:
    compress chunks older than 7 days.
    """

    __tablename__ = "observation_snapshots"

    snapshot_id: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # snapshot_timestamp is part of the PK because TimescaleDB hypertables
    # require the partitioning column in any unique index (same pattern as
    # market_bars.bar_start_time).
    snapshot_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    reserve0: Mapped[str] = mapped_column(Numeric, nullable=False)
    reserve1: Mapped[str] = mapped_column(Numeric, nullable=False)
    price: Mapped[str] = mapped_column(Numeric, nullable=False)
    liquidity_usd: Mapped[str | None] = mapped_column(Numeric, nullable=True)
    liquidity_usd_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    liquidity_usd_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_token_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    holder_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market_cap_usd: Mapped[str | None] = mapped_column(Numeric, nullable=True)
    fdv_usd: Mapped[str | None] = mapped_column(Numeric, nullable=True)

    __table_args__ = (
        Index(
            "ix_observation_snapshots_entity_time",
            "entity_id",
            "snapshot_timestamp",
            postgresql_using="btree",
        ),
    )


def _snapshot_to_row_values(snap: ObservationSnapshot) -> dict[str, object]:
    return {
        "snapshot_id": snap.snapshot_id,
        "schema_version": snap.schema_version,
        "entity_id": snap.entity_id,
        "chain_id": snap.chain_id,
        "snapshot_timestamp": snap.snapshot_timestamp,
        "observed_at": snap.observed_at,
        "ingested_at": snap.ingested_at,
        "source": snap.source,
        "snapshot_version": snap.snapshot_version,
        "reserve0": snap.reserve0,
        "reserve1": snap.reserve1,
        "price": snap.price,
        "liquidity_usd": snap.liquidity_usd,
        "liquidity_usd_source": snap.liquidity_usd_source,
        "liquidity_usd_confidence": snap.liquidity_usd_confidence,
        "quote_token_type": snap.quote_token_type,
        "holder_count": snap.holder_count,
        "market_cap_usd": snap.market_cap_usd,
        "fdv_usd": snap.fdv_usd,
    }


def _row_to_snapshot(row: ObservationSnapshotRow) -> ObservationSnapshot:
    return ObservationSnapshot(
        snapshot_id=row.snapshot_id,
        entity_id=row.entity_id,
        chain_id=row.chain_id,
        snapshot_timestamp=_ensure_utc(row.snapshot_timestamp),
        observed_at=_ensure_utc(row.observed_at),
        ingested_at=_ensure_utc(row.ingested_at),
        source=row.source,
        snapshot_version=row.snapshot_version,
        reserve0=str(row.reserve0),
        reserve1=str(row.reserve1),
        price=str(row.price),
        liquidity_usd=str(row.liquidity_usd) if row.liquidity_usd is not None else None,
        liquidity_usd_source=row.liquidity_usd_source,
        liquidity_usd_confidence=(
            float(row.liquidity_usd_confidence)
            if row.liquidity_usd_confidence is not None
            else None
        ),
        quote_token_type=row.quote_token_type,
        holder_count=row.holder_count,
        market_cap_usd=str(row.market_cap_usd) if row.market_cap_usd is not None else None,
        fdv_usd=str(row.fdv_usd) if row.fdv_usd is not None else None,
    )


async def save_snapshot(session: AsyncSession, snap: ObservationSnapshot) -> bool:
    """Upsert an ObservationSnapshot (INSERT ON CONFLICT UPDATE on
    snapshot_id). DOC-012 § B.3 composite key includes source so two
    sources snapshotting the same entity at the same instant never collide."""
    stmt = (
        pg_insert(ObservationSnapshotRow)
        .values(**_snapshot_to_row_values(snap))
        .on_conflict_do_update(
            index_elements=["snapshot_id", "snapshot_timestamp"],
            set_={
                k: v
                for k, v in _snapshot_to_row_values(snap).items()
                if k not in ("snapshot_id", "snapshot_timestamp")
            },
        )
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save ObservationSnapshot {snap.snapshot_id}") from exc
    return bool(result.rowcount == 1)


async def get_latest_snapshot(session: AsyncSession, entity_id: str) -> ObservationSnapshot | None:
    """Most recent snapshot for an entity (DOC-014 § Indexing Strategy)."""
    stmt = (
        select(ObservationSnapshotRow)
        .where(ObservationSnapshotRow.entity_id == entity_id)
        .order_by(ObservationSnapshotRow.snapshot_timestamp.desc())
        .limit(1)
    )
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(
            f"failed to read latest ObservationSnapshot for {entity_id}"
        ) from exc
    return _row_to_snapshot(row) if row is not None else None


async def list_snapshots(
    session: AsyncSession,
    entity_id: str,
    from_time: datetime,
    to_time: datetime,
) -> list[ObservationSnapshot]:
    """List snapshots for an entity in a time range."""
    stmt = (
        select(ObservationSnapshotRow)
        .where(
            ObservationSnapshotRow.entity_id == entity_id,
            ObservationSnapshotRow.snapshot_timestamp >= from_time,
            ObservationSnapshotRow.snapshot_timestamp < to_time,
        )
        .order_by(ObservationSnapshotRow.snapshot_timestamp)
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list ObservationSnapshots for {entity_id}") from exc
    return [_row_to_snapshot(row) for row in rows]


# ---------------------------------------------------------------------------
# Feature (DOC-012 § B.3)
# ---------------------------------------------------------------------------

# Entity types for Feature.entity_type (DOC-012 § B.3).
_ENTITY_TYPE_ENUM_NAME = "entity_type_feature_enum"


class FeatureRow(TimescaleBase):
    """Feature hypertable (DOC-012 § B.3, DOC-014 § TimescaleDB Hypertables).

    Partitioned by as_of_timestamp (1-day chunks). Compression policy:
    compress chunks older than 7 days.

    value is DOUBLE PRECISION — one of only two genuinely float fields in
    the schema set (DOC-014 § Type Mapping Rules, "Genuinely float";
    DOC-012 § Clarifying an ambiguity in DOC-008).
    """

    __tablename__ = "features"

    feature_id: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    feature_name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    # as_of_timestamp is part of the PK because TimescaleDB hypertables
    # require the partitioning column in any unique index.
    as_of_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DOUBLE PRECISION — genuinely float (DOC-014, DOC-012 § Clarifying
    # an ambiguity). All other financial fields remain Decimal/str.
    value: Mapped[float] = mapped_column(nullable=False)
    # Traceability: IDs of source Snapshots/Bars (DOC-012 § Traceability
    # Chain).
    inputs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    __table_args__ = (
        Index(
            "ix_features_entity_name_time",
            "entity_id",
            "feature_name",
            "as_of_timestamp",
            postgresql_using="btree",
        ),
    )


def _feature_to_row_values(feat: Feature) -> dict[str, object]:
    return {
        "feature_id": feat.feature_id,
        "schema_version": feat.schema_version,
        "feature_name": feat.feature_name,
        "entity_id": feat.entity_id,
        "entity_type": feat.entity_type,
        "as_of_timestamp": feat.as_of_timestamp,
        "computed_at": feat.computed_at,
        "window": feat.window,
        "value": feat.value,
        "inputs": feat.inputs,
    }


def _row_to_feature(row: FeatureRow) -> Feature:
    from onchain_platform.domain.schemas.feature import Feature

    return Feature(
        feature_id=row.feature_id,
        feature_name=row.feature_name,
        entity_id=row.entity_id,
        entity_type=row.entity_type,
        as_of_timestamp=_ensure_utc(row.as_of_timestamp),
        computed_at=_ensure_utc(row.computed_at),
        window=row.window,
        value=row.value,
        inputs=row.inputs,
    )


async def save_feature(session: AsyncSession, feat: Feature) -> bool:
    """Upsert a Feature (INSERT ON CONFLICT UPDATE on composite key).

    Idempotent re-computation: if the same feature_name + entity_id +
    as_of_timestamp is computed again, the latest values overwrite.
    """
    stmt = (
        pg_insert(FeatureRow)
        .values(**_feature_to_row_values(feat))
        .on_conflict_do_update(
            index_elements=["feature_id", "as_of_timestamp"],
            set_={
                k: v
                for k, v in _feature_to_row_values(feat).items()
                if k not in ("feature_id", "as_of_timestamp")
            },
        )
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save Feature {feat.feature_id}") from exc
    return bool(result.rowcount == 1)


async def get_feature_at(
    session: AsyncSession,
    entity_id: str,
    feature_name: str,
    as_of: datetime | None = None,
) -> Feature | None:
    """Point-in-Time query: most recent Feature with as_of_timestamp <= as_of.

    DOC-012 § B.3: 'as_of_timestamp — The point-in-time this value is
    valid for. This is the field every PIT-correctness query filters on.'
    DOC-014 § Indexing Strategy: (entity_id, feature_name, as_of_timestamp
    DESC).
    """

    if as_of is None:
        as_of = datetime.now(UTC)

    stmt = (
        select(FeatureRow)
        .where(
            FeatureRow.entity_id == entity_id,
            FeatureRow.feature_name == feature_name,
            FeatureRow.as_of_timestamp <= as_of,
        )
        .order_by(FeatureRow.as_of_timestamp.desc())
        .limit(1)
    )
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to query Feature {feature_name} for {entity_id}") from exc
    return _row_to_feature(row) if row is not None else None


async def list_features(
    session: AsyncSession,
    entity_id: str,
    feature_name: str,
    from_time: datetime,
    to_time: datetime,
) -> list[Feature]:
    """List Features for an entity in a time range."""

    stmt = (
        select(FeatureRow)
        .where(
            FeatureRow.entity_id == entity_id,
            FeatureRow.feature_name == feature_name,
            FeatureRow.as_of_timestamp >= from_time,
            FeatureRow.as_of_timestamp < to_time,
        )
        .order_by(FeatureRow.as_of_timestamp)
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list Features for {entity_id}") from exc
    return [_row_to_feature(row) for row in rows]


# ---------------------------------------------------------------------------
# Paged reads for the Research API (DOC-015 cursor pagination)
# ---------------------------------------------------------------------------


async def list_bars_page(
    session: AsyncSession,
    pair_id: str,
    interval: BarInterval,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    include_provisional: bool = False,
    cursor: dict[str, object] | None = None,
    limit: int = 100,
) -> tuple[list[MarketBar], dict[str, object] | None]:
    """Paged Market Bars for a pair, keyset on bar_start_time ASC.

    Serves `GET /v1/pairs/{id}/bars` (DOC-015). `include_provisional`
    defaults to false — provisional bars are never for research datasets
    (DOC-012 § B.3). Returns (items, next_cursor_keys).
    """
    stmt = select(MarketBarRow).where(
        MarketBarRow.pair_id == pair_id,
        MarketBarRow.interval == interval,
    )
    if start is not None:
        stmt = stmt.where(MarketBarRow.bar_start_time >= start)
    if end is not None:
        stmt = stmt.where(MarketBarRow.bar_start_time <= end)
    if not include_provisional:
        stmt = stmt.where(MarketBarRow.is_provisional.is_(False))
    if cursor is not None:
        last_ts = datetime.fromisoformat(str(cursor["bar_start_time"]))
        stmt = stmt.where(MarketBarRow.bar_start_time > last_ts)

    stmt = stmt.order_by(MarketBarRow.bar_start_time.asc()).limit(limit + 1)
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list MarketBars for {pair_id}") from exc

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = cast(
        "dict[str, object] | None",
        ({"bar_start_time": page[-1].bar_start_time.isoformat()} if has_more and page else None),
    )
    return [_row_to_bar(r) for r in page], next_cursor


async def list_snapshots_page(
    session: AsyncSession,
    entity_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    cursor: dict[str, object] | None = None,
    limit: int = 100,
) -> tuple[list[ObservationSnapshot], dict[str, object] | None]:
    """Paged Observation Snapshots, keyset on snapshot_timestamp ASC.

    Serves `GET /v1/entities/{id}/snapshots` (DOC-015). Returns
    (items, next_cursor_keys).
    """
    stmt = select(ObservationSnapshotRow).where(ObservationSnapshotRow.entity_id == entity_id)
    if start is not None:
        stmt = stmt.where(ObservationSnapshotRow.snapshot_timestamp >= start)
    if end is not None:
        stmt = stmt.where(ObservationSnapshotRow.snapshot_timestamp <= end)
    if cursor is not None:
        last_ts = datetime.fromisoformat(str(cursor["snapshot_timestamp"]))
        stmt = stmt.where(ObservationSnapshotRow.snapshot_timestamp > last_ts)

    stmt = stmt.order_by(ObservationSnapshotRow.snapshot_timestamp.asc()).limit(limit + 1)
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list ObservationSnapshots for {entity_id}") from exc

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = cast(
        "dict[str, object] | None",
        (
            {"snapshot_timestamp": page[-1].snapshot_timestamp.isoformat()}
            if has_more and page
            else None
        ),
    )
    return [_row_to_snapshot(r) for r in page], next_cursor


async def list_features_range(
    session: AsyncSession,
    entity_id: str,
    *,
    feature_names: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Feature]:
    """Features for an entity over a time range (dataset assembly).

    Serves the `features` slice of `GET /v1/pairs/{id}/dataset` (DOC-015).
    Deterministic order by (feature_name, as_of_timestamp) (DOC-013). This
    is a range query, not a paged collection — DOC-015 exposes no cursor
    for features; the dataset endpoint bounds the range instead.
    """
    stmt = select(FeatureRow).where(FeatureRow.entity_id == entity_id)
    if feature_names is not None:
        stmt = stmt.where(FeatureRow.feature_name.in_(feature_names))
    if start is not None:
        stmt = stmt.where(FeatureRow.as_of_timestamp >= start)
    if end is not None:
        stmt = stmt.where(FeatureRow.as_of_timestamp <= end)
    stmt = stmt.order_by(FeatureRow.feature_name, FeatureRow.as_of_timestamp)
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list Features for {entity_id}") from exc
    return [_row_to_feature(r) for r in rows]


async def list_feature_names(session: AsyncSession, entity_id: str) -> list[str]:
    """Distinct feature_names for an entity (used by dataset endpoints)."""
    stmt = (
        select(distinct(FeatureRow.feature_name))
        .where(FeatureRow.entity_id == entity_id)
        .order_by(FeatureRow.feature_name)
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list feature names for {entity_id}") from exc
    return list(rows)


async def list_latest_features(
    session: AsyncSession, entity_id: str, as_of: datetime
) -> list[Feature]:
    """Latest-per-name Features for an entity as of `as_of`.

    Serves `GET /v1/entities/{id}/features?as_of=...` (DOC-015 § PIT
    multi-feature form). One index seek per distinct feature_name, not one
    total — DOC-015 documents this as a heavier query than the single-name
    form. Deterministic order by feature_name (DOC-013).
    """
    names = await list_feature_names(session, entity_id)
    latest: list[Feature] = []
    for name in names:
        feat = await get_feature_at(session, entity_id, name, as_of)
        if feat is not None:
            latest.append(feat)
    return latest
