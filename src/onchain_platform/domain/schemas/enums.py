"""Fact-lifecycle enums (DOC-012 § B.1).

File location per DOC-011 v1.5 § domain/: `schemas/enums.py` holds the
fact-lifecycle enums (ConfirmationStatus, FactType); the structural
registry enums (ChainId, EntityType) live in `domain/enums.py` (Part A,
Milestone 4).
"""

from enum import StrEnum


class ConfirmationStatus(StrEnum):
    """The Confirmation Lifecycle (DOC-008 § Fact, ADR-006 § Confirmation
    Lifecycle): Pending → Confirmed → Finalized, or Orphaned.

    Facts become immutable only after Finalization (DOC-006).
    """

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FINALIZED = "FINALIZED"
    ORPHANED = "ORPHANED"


class FactType(StrEnum):
    """The four canonical fact types (DOC-012 § B.1).

    Naming note (DOC-012 § B.1): DOC-006's examples list both Mint/Burn and
    LiquidityAdded/LiquidityRemoved. These are not four distinct fact types —
    Mint/Burn are the *raw* Uniswap-V2-style event names; LIQUIDITY_ADDED /
    LIQUIDITY_REMOVED are the *canonical* fact_type values they normalize
    into. Only these four values may ever appear in a persisted
    BlockchainFact.fact_type.
    """

    PAIR_CREATED = "PAIR_CREATED"
    SWAP_EXECUTED = "SWAP_EXECUTED"
    LIQUIDITY_ADDED = "LIQUIDITY_ADDED"
    LIQUIDITY_REMOVED = "LIQUIDITY_REMOVED"


class BarInterval(StrEnum):
    """Market Bar intervals (DOC-012 § B.3).

    Epoch-based modulo arithmetic for bucketing: bar_start = event_time -
    (event_time % interval_seconds). Deterministic, timezone-independent
    (UTC).
    """

    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"

    @property
    def seconds(self) -> int:
        return {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
        }[self.value]


class EntityType(StrEnum):
    """Entity types for Feature.entity_type (DOC-012 § B.3)."""

    TRADING_PAIR = "TRADING_PAIR"
    WALLET = "WALLET"
    TOKEN = "TOKEN"
