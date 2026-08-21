"""ObservationSnapshot schema (DOC-012 § B.3).

The historically-preserved recording of State — this is what makes State
auditable after the fact. Stored in TimescaleDB (DOC-014 § Storage
Assignment).

snapshot_id uses '|' delimiter (DOC-012 § Composite ID Delimiter) so
two sources snapshotting the same entity at the same instant never collide.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ObservationSnapshot(BaseModel):
    """Timestamped recording of State (DOC-012 § B.3).

    Frozen like every Canonical Schema (DOC-013 § Immutability & State
    Modeling).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    # f"{entity_id}|{snapshot_timestamp.isoformat()}|{source}" — '|'
    # delimiter, not ':' (DOC-012 § Composite ID Delimiter).
    snapshot_id: str
    entity_id: str
    chain_id: int = Field(gt=0)
    snapshot_timestamp: datetime  # the moment this state describes
    observed_at: datetime
    ingested_at: datetime
    source: str  # e.g. "projection_engine:poll:60s"
    snapshot_version: int = Field(ge=0, default=1)
    # Token Amounts — Decimal-as-string (DOC-008 § Financial Precision).
    reserve0: str
    reserve1: str
    price: str
    # M5: all None (require external price oracle / token transfer events).
    liquidity_usd: str | None = None
    holder_count: int | None = None
    market_cap_usd: str | None = None
    fdv_usd: str | None = None

    @field_validator("reserve0", "reserve1")
    @classmethod
    def _validate_token_amount(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError(f"Token Amount must be non-negative integer string: {value!r}")
        return value

    @field_validator("snapshot_timestamp", "observed_at", "ingested_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware (UTC), got naive datetime")
        return value

    @classmethod
    def create(
        cls,
        *,
        entity_id: str,
        chain_id: int,
        snapshot_timestamp: datetime,
        observed_at: datetime,
        ingested_at: datetime,
        source: str,
        reserve0: str,
        reserve1: str,
        price: str,
    ) -> "ObservationSnapshot":
        """Factory that computes snapshot_id from components."""
        snapshot_id = f"{entity_id}|{snapshot_timestamp.isoformat()}|{source}"
        return cls(
            snapshot_id=snapshot_id,
            entity_id=entity_id,
            chain_id=chain_id,
            snapshot_timestamp=snapshot_timestamp,
            observed_at=observed_at,
            ingested_at=ingested_at,
            source=source,
            reserve0=reserve0,
            reserve1=reserve1,
            price=price,
        )
