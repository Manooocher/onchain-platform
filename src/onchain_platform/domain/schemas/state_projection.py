"""StateProjection schema (DOC-012 § B.2).

The live, mutable, continuously-recomputed read model. Never persisted as
its own historical table — served from Redis cache (DOC-010) and always
rebuildable by replaying Facts (DOC-006: "State can always be reconstructed
by replaying Facts").

All reserve/price fields are Decimal-as-string (DOC-008 § Financial
Precision Principle). Price is always token1 per token0 (DOC-012 § B.2).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StateProjection(BaseModel):
    """Live pool state (DOC-012 § B.2).

    Frozen like every Canonical Schema (DOC-013 § Immutability & State
    Modeling). State change is model_copy(update=...), never mutation.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    entity_id: str  # Canonical ID of the LiquidityPool
    chain_id: int = Field(gt=0)
    as_of_block: int = Field(ge=0)
    as_of_fact_id: str  # last BlockchainFact.fact_id that updated this
    computed_at: datetime
    # Token Amounts — raw smallest-denomination integers as strings
    # (DOC-008 § Token Amount). Never float.
    reserve0: str
    reserve1: str
    # token1 per token0, Decimal-as-string (DOC-012 § B.2).
    price: str

    @field_validator("reserve0", "reserve1")
    @classmethod
    def _validate_token_amount(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError(f"Token Amount must be non-negative integer string: {value!r}")
        return value

    @field_validator("price")
    @classmethod
    def _validate_price(cls, value: str) -> str:
        from decimal import Decimal, InvalidOperation

        try:
            d = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"price must be a valid Decimal string: {value!r}") from exc
        if d < 0:
            raise ValueError(f"price must be non-negative: {value!r}")
        return value
