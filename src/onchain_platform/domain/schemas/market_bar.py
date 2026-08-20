"""MarketBar schema (DOC-012 § B.3).

Derived exclusively from finalized SWAP_EXECUTED facts (DOC-006 — never
from ObservationSnapshot). This is the field that makes a Market Bar
reproducible and auditable, not just plausible.

All OHLCV fields are Decimal-as-string (DOC-008 § Financial Precision
Principle). Never float, never a native JSON number.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from onchain_platform.domain.schemas.enums import BarInterval


class MarketBar(BaseModel):
    """Aggregated market activity over a fixed time window (DOC-012 § B.3).

    Derived exclusively from finalized SWAP_EXECUTED facts (DOC-006 §
    Market Data Pipeline — never from ObservationSnapshot).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    # bar_id: f"{pair_id}|{interval}|{bar_start_time.isoformat()}" — '|'
    # delimiter, not ':' (DOC-012 § Composite ID Delimiter).
    bar_id: str
    pair_id: str  # Canonical ID of the TradingPair
    chain_id: int = Field(gt=0)
    interval: BarInterval
    bar_start_time: datetime  # Bucket start, event_time-based
    bar_end_time: datetime
    # OHLCV — all Decimal-as-string (DOC-008 § Financial Precision).
    open: str
    high: str
    low: str
    close: str
    volume_base: str
    volume_quote: str
    trade_count: int = Field(ge=0)
    vwap: str
    buy_volume: str
    sell_volume: str
    # (first_fact_id, last_fact_id) — every fact between these, inclusive,
    # composed this bar. For audit, not for reconstruction (DOC-012 § B.3:
    # "the reconstruction predicate, not the source_fact_range bounds
    # alone, is the authoritative definition").
    source_fact_range: tuple[str, str]
    # true if built from CONFIRMED-but-not-yet-FINALIZED facts (allowed
    # only for low-latency dashboard use per DOC-007, never for research
    # datasets). M3: always False (FINALIZED only).
    is_provisional: bool = False
    computed_at: datetime

    @field_validator("bar_start_time", "bar_end_time", "computed_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware (UTC), got naive datetime")
        return value

    @field_validator("bar_end_time")
    @classmethod
    def _end_after_start(cls, value: datetime, info: object) -> datetime:
        # Validated after all fields are set via model_validator if needed.
        # For now, just ensure it's timezone-aware (above).
        return value

    @classmethod
    def create(
        cls,
        *,
        pair_id: str,
        chain_id: int,
        interval: BarInterval,
        bar_start_time: datetime,
        open_: str,
        high: str,
        low: str,
        close: str,
        volume_base: str,
        volume_quote: str,
        trade_count: int,
        vwap: str,
        buy_volume: str,
        sell_volume: str,
        source_fact_range: tuple[str, str],
        is_provisional: bool = False,
        computed_at: datetime,
    ) -> "MarketBar":
        """Factory that computes bar_id from components (DOC-012 § B.3)."""
        bar_end_time = _add_interval(bar_start_time, interval)
        bar_id = f"{pair_id}|{interval.value}|{bar_start_time.isoformat()}"
        return cls(
            bar_id=bar_id,
            pair_id=pair_id,
            chain_id=chain_id,
            interval=interval,
            bar_start_time=bar_start_time,
            bar_end_time=bar_end_time,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume_base=volume_base,
            volume_quote=volume_quote,
            trade_count=trade_count,
            vwap=vwap,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            source_fact_range=source_fact_range,
            is_provisional=is_provisional,
            computed_at=computed_at,
        )


def _add_interval(dt: datetime, interval: BarInterval) -> datetime:
    """Add one interval duration to a datetime."""
    from datetime import timedelta

    return dt + timedelta(seconds=interval.seconds)
