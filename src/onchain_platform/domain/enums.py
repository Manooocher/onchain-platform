"""Structural/registry enums (DOC-012 Part A, DOC-011 v1.5 § domain/).

File location per DOC-011 v1.5: `domain/enums.py` holds structural enums
(ContractType, EntityType); `domain/schemas/enums.py` holds fact-lifecycle
enums (ConfirmationStatus, FactType, BarInterval).
"""

from enum import StrEnum


class ContractType(StrEnum):
    """SmartContract contract_type (DOC-012 Part A SmartContract)."""

    ERC20 = "ERC20"
    FACTORY = "FACTORY"
    ROUTER = "ROUTER"
    POOL = "POOL"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(StrEnum):
    """Metadata verification_status (DOC-012 Part A Metadata)."""

    UNVERIFIED = "UNVERIFIED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
