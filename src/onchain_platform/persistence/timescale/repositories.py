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

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    Numeric,
    Text,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from onchain_platform.domain.exceptions import PersistenceError
from onchain_platform.domain.schemas.enums import BarInterval
from onchain_platform.domain.schemas.market_bar import MarketBar


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
    interval: Mapped[BarInterval] = mapped_column(
        Enum(BarInterval, name="bar_interval_enum", native_enum=True),
        nullable=False,
    )
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
        "interval": bar.interval,
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
        interval=row.interval,
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
