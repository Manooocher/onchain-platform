"""Canonical ID construction (DOC-012 Part A, DOC-008 § Canonical ID).

Format: eip155:<chain_id>/<entity_type>:<checksummed_address>

Addresses are always EIP-55 checksummed — a schema-level validator, not a
convention left to callers (DOC-012 § Conventions). eth_utils.to_checksum_address
is a pure function (no I/O), approved for domain use (Milestone 1 planning Q3).

Canonical IDs are stable across reorgs — EVM addresses are deterministic from
deployment tx hash + sender nonce; no reorg can change them.
"""

from eth_utils.address import to_checksum_address


def token_canonical_id(chain_id: int, address: str) -> str:
    """eip155:<chain_id>/token:<checksummed_address> (DOC-012 Part A Token)."""
    return f"eip155:{chain_id}/token:{to_checksum_address(address)}"


def pair_canonical_id(chain_id: int, pool_address: str) -> str:
    """eip155:<chain_id>/pair:<checksummed_address> (DOC-012 Part A TradingPair)."""
    return f"eip155:{chain_id}/pair:{to_checksum_address(pool_address)}"


def wallet_canonical_id(chain_id: int, address: str) -> str:
    """eip155:<chain_id>/wallet:<checksummed_address> (DOC-012 Part A Wallet)."""
    return f"eip155:{chain_id}/wallet:{to_checksum_address(address)}"


def smart_contract_canonical_id(chain_id: int, address: str) -> str:
    """eip155:<chain_id>/contract:<checksummed_address> (DOC-012 Part A SmartContract)."""
    return f"eip155:{chain_id}/contract:{to_checksum_address(address)}"
