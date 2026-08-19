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
