"""SmartContract entity (DOC-012 Part A) — executable on-chain logic.

Slowly-changing registry object (DOC-006 § Structural Domain). Frozen like
every Canonical Schema (DOC-013 § Immutability & State Modeling).
"""

from pydantic import BaseModel, ConfigDict, Field

from onchain_platform.domain.enums import ContractType


class SmartContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    canonical_id: str  # eip155:<chain_id>/contract:<address>
    chain_id: int = Field(gt=0)
    address: str  # EIP-55 checksummed
    contract_type: ContractType
    is_verified: bool = False
    deployment_block: int | None = None
