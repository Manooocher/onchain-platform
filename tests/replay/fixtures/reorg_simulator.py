"""ReorgSimulatorProvider — test infrastructure for reorg replay tests.

Serves two 'views' of the same block range: a canonical chain on the first
pass, and a divergent chain from a configurable fork point on subsequent
passes. This is deterministic, self-contained test infrastructure — not
production code (placed in tests/replay/fixtures/, not
acquisition/providers/).

The divergent chain has different block_hash values for blocks after the
fork point, but maintains valid parent_hash continuity on the new branch.
This simulates a real reorg where the chain converges on a different
canonical history.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from onchain_platform.acquisition.providers.base import (
    BlockchainProvider,
    BlockMetadata,
    RawLog,
)
from onchain_platform.domain.exceptions import AcquisitionError


class ReorgSimulatorProvider(BlockchainProvider):
    """BlockchainProvider that serves canonical then divergent chains.

    Pass 0: serves the canonical chain (all blocks).
    Pass 1+: serves the divergent chain for blocks > fork_point,
             canonical chain for blocks <= fork_point.
    """

    def __init__(
        self,
        canonical_blocks: dict[int, BlockMetadata],
        divergent_blocks: dict[int, BlockMetadata],
        fork_point: int,
        logs: dict[int, list[RawLog]] | None = None,
    ) -> None:
        self._canonical = canonical_blocks
        self._divergent = divergent_blocks
        self._fork_point = fork_point
        self._logs = logs or {}
        self._pass_count = 0

    def advance_pass(self) -> None:
        """Simulate the moment when the reorg is detected."""
        self._pass_count += 1

    def _blocks_for_current_pass(self) -> dict[int, BlockMetadata]:
        if self._pass_count == 0:
            return self._canonical
        # After reorg: divergent chain for blocks > fork_point,
        # canonical for blocks <= fork_point.
        result = {k: v for k, v in self._canonical.items() if k <= self._fork_point}
        result.update({k: v for k, v in self._divergent.items() if k > self._fork_point})
        return result

    async def get_chain_id(self) -> int:
        return 8453

    async def get_chain_head(self) -> int:
        return max(self._blocks_for_current_pass().keys())

    async def get_block_metadata(self, block_number: int) -> BlockMetadata:
        blocks = self._blocks_for_current_pass()
        try:
            return blocks[block_number]
        except KeyError as exc:
            raise AcquisitionError(f"no block {block_number}") from exc

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> list[RawLog]:
        result: list[RawLog] = []
        for bn in range(from_block, to_block + 1):
            result.extend(self._logs.get(bn, []))
        result.sort(key=lambda log: (log.block_number, log.log_index))
        return result

    async def close(self) -> None:
        return None


def make_canonical_chain(start: int, count: int) -> dict[int, BlockMetadata]:
    """Build a canonical chain with valid parent_hash continuity."""
    blocks = {}
    for i in range(count):
        num = start + i
        blocks[num] = BlockMetadata(
            number=num,
            hash=f"0x{num:064x}",
            parent_hash=f"0x{num - 1:064x}",
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        )
    return blocks


def make_divergent_chain(
    fork_point: int, count: int, hash_prefix: str = "dd"
) -> dict[int, BlockMetadata]:
    """Build a divergent chain starting from fork_point + 1.

    The first divergent block has parent_hash = fork_point's hash (from the
    canonical chain). Subsequent blocks chain from each other.
    """
    blocks = {}
    for i in range(count):
        num = fork_point + 1 + i
        parent = fork_point if i == 0 else num - 1
        blocks[num] = BlockMetadata(
            number=num,
            hash=f"0x{hash_prefix}{num:062x}",
            parent_hash=f"0x{parent:064x}" if i == 0 else f"0x{hash_prefix}{parent:062x}",
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        )
    return blocks
