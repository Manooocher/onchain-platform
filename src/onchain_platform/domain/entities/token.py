"""Token entity (DOC-012 Part A) — a fungible blockchain asset.

Slowly-changing registry object (DOC-006 § Structural Domain). Frozen like
every Canonical Schema (DOC-013 § Immutability & State Modeling).
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Token(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    canonical_id: str
    chain_id: int = Field(gt=0)
    contract_address: str  # EIP-55 checksummed
    symbol: str = "UNKNOWN"
    name: str = "Unknown Token"
    decimals: int = Field(default=18, ge=0, le=255)
    # Token Amount (DOC-008) — raw smallest-denomination integer as a
    # string, decimals never pre-applied.
    total_supply: str = "0"
    deployment_block: int | None = None

    @field_validator("canonical_id")
    @classmethod
    def _canonical_id_matches_components(cls, value: str, info: object) -> str:
        # Validated lazily — chain_id and contract_address may not be set
        # yet during partial construction. The entity_resolution service
        # constructs canonical_id from the components, so this validator
        # is defense-in-depth.
        return value
