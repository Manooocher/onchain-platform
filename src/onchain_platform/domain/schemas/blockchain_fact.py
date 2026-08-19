"""BlockchainFact — the canonical, versioned envelope for every finalized
(or finalizing) piece of blockchain history (DOC-012 § B.1).

Typed directly from DOC-012 § B.1 — field for field, type for type. If a
field needed here is not listed there, it does not exist yet: add it to
DOC-012 first, then here (DOC-012 § Purpose).

All models are frozen (DOC-013 § Immutability & State Modeling: every
Pydantic model under domain/ sets frozen=True unconditionally — a Pending
fact is exactly as frozen an object as a Finalized one; state change is
always model_copy(update=...), never mutation).

All validators are pure functions — no I/O, no wall-clock time (DOC-013 §
Immutability & State Modeling: Pydantic validators must be pure functions).
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from eth_utils.address import to_checksum_address
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType

# EIP-55 checksummed address: 0x + 40 hex (DOC-014 § Standard mappings —
# VARCHAR(42), "a checksummed EVM address is always exactly 42 characters").
_HEX_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
# tx_hash / block_hash: 0x + 64 hex (DOC-014 — VARCHAR(66)). Lowercase is
# the canonical wire form returned by JSON-RPC; the normalizer lowercases
# before construction, and the schema pins that canonical form rather than
# silently storing whatever case a provider happened to emit.
_HEX_HASH_RE = re.compile(r"^0x[0-9a-f]{64}$")
# Token Amount: raw on-chain integer in the smallest denomination, as a
# string (DOC-008 § Token Amount, DOC-012 § SWAP_EXECUTED payload). Never a
# float, never a native JSON number, never with decimals pre-applied.
_TOKEN_AMOUNT_RE = re.compile(r"^[0-9]+$")


def _validate_checksummed_address(value: str, field_name: str) -> str:
    """Enforce EIP-55 checksummed form — a schema-level validator, not a
    convention left to callers (DOC-012 § Conventions).

    eth_utils.to_checksum_address is a pure function (approved for domain
    validators — Milestone 1 planning Q3): it recomputes the checksum from
    the address bytes with no I/O, satisfying DOC-013 validator purity.
    """
    if not _HEX_ADDRESS_RE.match(value):
        raise ValueError(f"{field_name} is not a 0x-prefixed 40-hex EVM address: {value!r}")
    expected = to_checksum_address(value)
    if value != expected:
        raise ValueError(
            f"{field_name} is not EIP-55 checksummed: got {value!r}, expected {expected!r}"
        )
    return value


def _validate_token_amount(value: str, field_name: str, *, positive: bool = False) -> str:
    """Token Amounts are strings of decimal digits (DOC-008 § Token Amount).

    A negative value is a validation error, never a direction encoding —
    direction comes exclusively from fact_type or from _in/_out field names
    (DOC-012 § B.1, liquidity payload notes).
    """
    if not _TOKEN_AMOUNT_RE.match(value):
        raise ValueError(
            f"{field_name} must be a non-negative integer string (raw smallest-denomination "
            f"Token Amount per DOC-008), got {value!r}"
        )
    if positive and int(value) <= 0:
        raise ValueError(f"{field_name} must be a positive magnitude, got {value!r}")
    return value


class _FrozenModel(BaseModel):
    """frozen=True for every Canonical Schema, unconditionally
    (DOC-013 § Immutability & State Modeling)."""

    model_config = ConfigDict(frozen=True)


class PairCreatedPayload(_FrozenModel):
    """PAIR_CREATED payload (DOC-012 § B.1). No Token Amount fields — this
    is why PairCreated is Milestone 1's first fact type (ImplementationPlan
    § Milestone 1: least possible surface area, no Financial Precision
    questions)."""

    fact_type: Literal["PAIR_CREATED"]
    pair_address: str
    token0_address: str
    token1_address: str
    dex: str

    @field_validator("pair_address", "token0_address", "token1_address")
    @classmethod
    def _checksummed(cls, value: str, info: object) -> str:
        return _validate_checksummed_address(value, str(getattr(info, "field_name", "address")))


class SwapExecutedPayload(_FrozenModel):
    """SWAP_EXECUTED payload (DOC-012 § B.1). Every amount is a Token Amount
    — Decimal-precise from the moment it is parsed, never float (DOC-008
    § Financial Precision Principle). Milestone 1 does not process this fact
    type; the class exists because the discriminated union below is one
    DOC-012 artifact."""

    fact_type: Literal["SWAP_EXECUTED"]
    pool_address: str
    sender: str
    recipient: str
    amount0_in: str
    amount1_in: str
    amount0_out: str
    amount1_out: str

    @field_validator("pool_address", "sender", "recipient")
    @classmethod
    def _checksummed(cls, value: str, info: object) -> str:
        return _validate_checksummed_address(value, str(getattr(info, "field_name", "address")))

    @field_validator("amount0_in", "amount1_in", "amount0_out", "amount1_out")
    @classmethod
    def _token_amounts(cls, value: str, info: object) -> str:
        # Direction is already unambiguous from the _in/_out field names
        # (DOC-012 § B.1) — zero is legal here, e.g. amount0_in="0".
        return _validate_token_amount(value, str(getattr(info, "field_name", "amount")))


class LiquidityAddedPayload(_FrozenModel):
    """LIQUIDITY_ADDED payload (DOC-012 § B.1). All amounts are positive
    magnitudes; direction comes exclusively from fact_type (DOC-012 § B.1
    liquidity payload notes)."""

    fact_type: Literal["LIQUIDITY_ADDED"]
    pool_address: str
    provider: str
    amount0: str
    amount1: str
    liquidity_delta: str

    @field_validator("pool_address", "provider")
    @classmethod
    def _checksummed(cls, value: str, info: object) -> str:
        return _validate_checksummed_address(value, str(getattr(info, "field_name", "address")))

    @field_validator("amount0", "amount1", "liquidity_delta")
    @classmethod
    def _positive_magnitudes(cls, value: str, info: object) -> str:
        return _validate_token_amount(
            value, str(getattr(info, "field_name", "amount")), positive=True
        )


class LiquidityRemovedPayload(_FrozenModel):
    """LIQUIDITY_REMOVED payload (DOC-012 § B.1). Same shape and magnitude
    rules as LIQUIDITY_ADDED — direction comes from fact_type, never from a
    negative sign."""

    fact_type: Literal["LIQUIDITY_REMOVED"]
    pool_address: str
    provider: str
    amount0: str
    amount1: str
    liquidity_delta: str

    @field_validator("pool_address", "provider")
    @classmethod
    def _checksummed(cls, value: str, info: object) -> str:
        return _validate_checksummed_address(value, str(getattr(info, "field_name", "address")))

    @field_validator("amount0", "amount1", "liquidity_delta")
    @classmethod
    def _positive_magnitudes(cls, value: str, info: object) -> str:
        return _validate_token_amount(
            value, str(getattr(info, "field_name", "amount")), positive=True
        )


class BlockchainFact(_FrozenModel):
    """The canonical, versioned envelope for blockchain history
    (DOC-012 § B.1)."""

    schema_version: str = "1.0"
    fact_id: str
    chain_id: int = Field(gt=0)
    fact_type: FactType
    block_number: int = Field(ge=0)
    block_hash: str
    tx_hash: str
    log_index: int = Field(ge=0)
    event_time: datetime  # block timestamp — actual chain time (DOC-008)
    observed_at: datetime  # when the RPC/provider emitted this to us
    ingested_at: datetime  # when our platform received it
    confirmation_status: ConfirmationStatus
    confirmations: int = Field(ge=0)
    payload: Annotated[
        (
            PairCreatedPayload
            | SwapExecutedPayload
            | LiquidityAddedPayload
            | LiquidityRemovedPayload
        ),
        # Each payload carries its own fact_type Literal so Pydantic can
        # dispatch without trying every union member in order — DOC-012
        # § Modeling the discriminated payload (mandatory pattern).
        Field(discriminator="fact_type"),
    ]

    @field_validator("tx_hash", "block_hash")
    @classmethod
    def _canonical_hash_form(cls, value: str, info: object) -> str:
        field_name = str(getattr(info, "field_name", "hash"))
        if not _HEX_HASH_RE.match(value):
            raise ValueError(
                f"{field_name} must be 0x + 64 lowercase hex (canonical JSON-RPC form), "
                f"got {value!r}"
            )
        return value

    @field_validator("event_time", "observed_at", "ingested_at")
    @classmethod
    def _timezone_aware(cls, value: datetime, info: object) -> datetime:
        # All three timestamps of the Triple Timestamp Standard are
        # timezone-aware UTC; a naive datetime is a validation error, not a
        # warning (DOC-012 § Conventions). Pydantic v2 accepts naive
        # datetimes by default, hence this explicit check.
        if value.tzinfo is None or value.utcoffset() is None:
            field_name = str(getattr(info, "field_name", "timestamp"))
            raise ValueError(f"{field_name} must be timezone-aware (UTC), got naive datetime")
        return value

    @field_validator("fact_id")
    @classmethod
    def _fact_id_components(cls, value: str, info: object) -> str:
        # Deterministic natural key: f"{chain_id}:{tx_hash}:{log_index}" —
        # no surrogate UUID (DOC-012 § B.1, ADR-006 § Idempotency). Splitting
        # on ':' always yields exactly three parts for a fact_id (DOC-012
        # § Composite ID Delimiter).
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"fact_id must have exactly three ':'-separated components "
                f"(chain_id:tx_hash:log_index), got {value!r}"
            )
        return value

    @field_serializer("event_time", "observed_at", "ingested_at")
    def _serialize_utc_z(self, value: datetime) -> str:
        # Timestamps serialize as ISO-8601 with a Z suffix (DOC-012 §
        # Conventions example: "2026-07-11T14:32:05Z"). Validators above
        # guarantee tz-awareness, so astimezone(UTC) is always safe.
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @model_validator(mode="after")
    def _fact_id_matches_components(self) -> "BlockchainFact":
        # The natural key must be exactly the components it claims to be —
        # a mismatch here means the fact_id was built from different data
        # than the row carries, which would break ADR-006 § Idempotency
        # (dedup keyed on fact_id would miss a genuine duplicate).
        expected = f"{self.chain_id}:{self.tx_hash}:{self.log_index}"
        if self.fact_id != expected:
            raise ValueError(f"fact_id {self.fact_id!r} does not match its components {expected!r}")
        return self
