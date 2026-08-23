"""API response envelopes (DOC-015 § Response Shape).

The ONLY bespoke response model in the API is the pagination envelope — every
resource body is a Canonical Schema directly. Single-resource GETs return the
schema body itself; collection endpoints return this envelope.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from onchain_platform.domain.entities.liquidity_pool import LiquidityPool
from onchain_platform.domain.entities.metadata import Metadata
from onchain_platform.domain.entities.smart_contract import SmartContract
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.entities.wallet import Wallet

T = TypeVar("T")


class PaginationInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    next_cursor: str | None = None
    has_more: bool = False


class PaginatedResponse(BaseModel, Generic[T]):  # noqa: UP046 — Pydantic needs Generic[T]
    model_config = ConfigDict(frozen=True)

    items: list[T]
    pagination: PaginationInfo = Field(default_factory=PaginationInfo)


# ---------------------------------------------------------------------------
# Nested compound responses (DOC-015 /pairs/{id}, /tokens/{id}).
#
# These compose Canonical Schemas (TradingPair + its LiquidityPool + Metadata)
# into one body per DOC-015 — they are aggregation of existing schemas, not
# parallel DTOs duplicating their fields.
# ---------------------------------------------------------------------------


class PairDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    pair: TradingPair
    liquidity_pool: LiquidityPool | None = None
    metadata: Metadata | None = None


class TokenDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    token: Token
    smart_contract: SmartContract | None = None
    metadata: Metadata | None = None


class WalletDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    wallet: Wallet
