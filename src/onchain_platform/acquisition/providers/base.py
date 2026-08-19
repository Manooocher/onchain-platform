"""BlockchainProvider — the abstract provider interface (ADR-006 § Provider
Abstraction).

The Collector must never depend on a specific RPC vendor. All providers
implement this interface; changing infrastructure providers must not require
changes to domain logic (ADR-006 Principle 6, DOC-010 § Blockchain
Connectivity).

The interface exposes ONLY blockchain primitives (ADR-006: "The interface is
responsible for exposing only blockchain primitives"). Business concepts such
as PairCreated or SwapExecuted must NEVER exist inside a provider
implementation — those belong to processing/ (ADR-006 § Provider
Abstraction).

Provider-specific types (web3 LogReceipt, raw JSON-RPC dicts, httpx
responses) never cross this boundary (DOC-011 § What Does Not Belong Here):
everything crossing it is one of the typed primitives below — normalized,
frozen, canonical wire form.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RawLog(BaseModel):
    """A normalized EVM log — the primitive every provider emits.

    Canonical wire form: lowercase 0x-hex hashes, integer block/log indices.
    Any provider-specific extras (e.g. the `blockTimestamp` field some
    endpoints return inside logs) are deliberately NOT modeled here — the
    block timestamp has exactly one canonical source, the block header
    (ADR-006 Principle 3: provider-specific behavior must never influence
    canonical outputs).
    """

    model_config = ConfigDict(frozen=True)

    address: str  # emitting contract, lowercase 0x + 40 hex
    topics: tuple[str, ...]  # 32-byte hex strings; topics[0] is the event
    data: str  # hex-encoded ABI payload
    block_number: int
    block_hash: str  # lowercase 0x + 64 hex
    transaction_hash: str  # lowercase 0x + 64 hex
    transaction_index: int
    log_index: int
    removed: bool  # True if the log was removed by a reorg on the provider


class BlockMetadata(BaseModel):
    """Block header fields the pipeline needs (ADR-006 § Provider
    Abstraction: 'Retrieve block metadata')."""

    model_config = ConfigDict(frozen=True)

    number: int
    hash: str  # lowercase 0x + 64 hex
    timestamp: datetime  # block timestamp, tz-aware UTC


class BlockchainProvider(ABC):
    """Abstract async provider.

    Every implementation:
    - carries an explicit timeout on every external call (DOC-013 § Async
      Conventions: no call may rely on a library default);
    - translates every provider/transport exception to AcquisitionError
      before it leaves acquisition/ (DOC-013 § Exception Hierarchy);
    - never raises for "no results" — an empty log set is an empty sequence.
    """

    @abstractmethod
    async def get_chain_id(self) -> int:
        """The provider's EIP-155 chain id (eth_chainId)."""

    @abstractmethod
    async def get_chain_head(self) -> int:
        """Latest block number known to the provider (eth_blockNumber)."""

    @abstractmethod
    async def get_block_metadata(self, block_number: int) -> BlockMetadata:
        """Header metadata for one block (eth_getBlockByNumber)."""

    @abstractmethod
    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> list[RawLog]:
        """Logs in [from_block, to_block] inclusive, filtered by emitting
        address and/or topic0 (eth_getLogs). Returns [] when nothing
        matches.

        Implementations must return logs in canonical order: block_number
        ascending, then log_index ascending (DOC-013 § Determinism
        Discipline — no reliance on provider return order).
        """

    @abstractmethod
    async def close(self) -> None:
        """Release underlying transport resources (httpx client, sockets).

        Called exactly once during graceful shutdown (DOC-013 § Async
        Conventions).
        """
