"""ChainReorgEvent schema (DOC-012 § B.5).

DOC-013 § Exception Hierarchy: "Reorgs are modeled as Domain Events (e.g.,
ChainReorgDetected containing fork block and depth) published to Redis
Streams, never as Exceptions." This schema is the typed contract for that
statement.

Not B.0–B.4: it isn't operational metadata, isn't append-only history,
isn't cached state, isn't an analytical time series, and isn't a ground-
truth artifact. It is consumed once, by whichever subscribers care, and
then it is gone — Redis Streams retention, not a table, is its only
"storage" (DOC-012 § B.5).

M2 constructs these objects in-memory and logs them via structlog. Actual
Redis publishing is wired when transport/event_stream.py arrives.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChainReorgEvent(BaseModel):
    """A chain reorganization detected by the Finality Engine (DOC-012 § B.5)."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    event_id: str
    chain_id: int = Field(gt=0)
    fork_block_number: int = Field(ge=0)
    orphaned_block_range: tuple[int, int]
    new_canonical_head_hash: str
    depth: int = Field(gt=0)
    detected_at: datetime

    @field_validator("event_id")
    @classmethod
    def _event_id_format(cls, value: str) -> str:
        # f"{chain_id}|{fork_block_number}|{detected_at.isoformat()}" —
        # '|' delimiter, not ':' (DOC-012 § Composite ID Delimiter).
        parts = value.split("|")
        if len(parts) != 3:
            raise ValueError(
                f"event_id must have exactly three '|'-separated components, got {value!r}"
            )
        return value

    @field_validator("orphaned_block_range")
    @classmethod
    def _orphaned_range_ordered(cls, value: tuple[int, int]) -> tuple[int, int]:
        first, last = value
        if first > last:
            raise ValueError(
                f"orphaned_block_range must be (first, last) with first <= last, got {value!r}"
            )
        return value

    @field_validator("new_canonical_head_hash")
    @classmethod
    def _canonical_hash_form(cls, value: str) -> str:
        if not value.startswith("0x") or len(value) != 66:
            raise ValueError(f"new_canonical_head_hash must be 0x + 64 hex, got {value!r}")
        return value.lower()

    @field_validator("detected_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("detected_at must be timezone-aware (UTC), got naive datetime")
        return value

    @classmethod
    def create(
        cls,
        *,
        chain_id: int,
        fork_block_number: int,
        orphaned_block_range: tuple[int, int],
        new_canonical_head_hash: str,
        depth: int,
        detected_at: datetime | None = None,
    ) -> "ChainReorgEvent":
        """Factory that computes event_id from components (DOC-012 § B.5)."""
        ts = detected_at or datetime.now(UTC)
        event_id = f"{chain_id}|{fork_block_number}|{ts.isoformat().replace('+00:00', 'Z')}"
        return cls(
            event_id=event_id,
            chain_id=chain_id,
            fork_block_number=fork_block_number,
            orphaned_block_range=orphaned_block_range,
            new_canonical_head_hash=new_canonical_head_hash,
            depth=depth,
            detected_at=ts,
        )
