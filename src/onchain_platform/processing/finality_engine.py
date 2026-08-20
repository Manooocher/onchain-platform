"""Finality & Canonical Chain Validation Engine (ADR-006 § Finality Engine).

DOC-011: 'finality_engine.py is the code with the highest correctness bar
in the repository — it is what the Replay Tests in tests/replay/ exist to
protect.'

ADR-006 § Canonical Chain Validation Engine: 'The Finality & Canonical
Chain Validation Engine does not validate continuity by comparing only the
parent hash of the most recently received block. Instead, it maintains an
in-memory buffer of the last N block headers, where N is the configured
confirmation depth for that chain. On every new block, the engine verifies
the continuity of the entire canonical chain across that confirmation
window — not a single block.'

Determinism (DOC-013 § Determinism Discipline): no wall-clock reads inside
this Capability. The clock is injected and used only for ChainReorgEvent
timestamps and checkpoint updated_at.

Single Processing Path (ADR-006): the same engine processes both live and
replayed blocks — no separate 'historical mode.'
"""

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.acquisition.providers.base import BlockchainProvider, BlockMetadata
from onchain_platform.domain.schemas.chain_reorg_event import ChainReorgEvent
from onchain_platform.domain.schemas.checkpoint import Checkpoint
from onchain_platform.persistence.postgres import repositories
from onchain_platform.processing.reorg_handler import ReorgEventHandler

logger = structlog.get_logger(__name__)


class FinalityEngine:
    """Verifies canonical chain continuity and advances the Confirmation
    Lifecycle (ADR-006 § Finality & Canonical Chain Validation Engine).

    Dependencies are injected (DOC-013 § Dependency & Composition):
    - provider: fetches block metadata for the header buffer
    - session_factory: creates async sessions (scoped to each call)
    - clock: produces timestamps (injected, never datetime.now())
    - reorg_handler: consumes ChainReorgEvent objects
    """

    def __init__(
        self,
        *,
        chain_id: int,
        confirmation_depth: int,
        provider: BlockchainProvider,
        engine: AsyncEngine,
        clock: Callable[[], datetime],
        reorg_handler: ReorgEventHandler,
    ) -> None:
        self._chain_id = chain_id
        self._confirmation_depth = confirmation_depth
        self._provider = provider
        self._engine = engine
        self._clock = clock
        self._reorg_handler = reorg_handler
        # In-memory header buffer of the last N block headers (ADR-006 §
        # Canonical Chain Validation Engine). maxlen=confirmation_depth
        # ensures we never hold more than the configured window.
        self._buffer: deque[BlockMetadata] = deque(maxlen=confirmation_depth)

    async def on_new_block(self, block_number: int) -> None:
        """Process one new block: verify continuity, advance lifecycle,
        detect reorgs.

        Called once per block by the collector, after all facts for that
        block are persisted as PENDING.
        """
        block = await self._provider.get_block_metadata(block_number)
        self._buffer.append(block)

        # Mandatory structured fields for processing/ (DOC-013 §
        # Observability in Code).
        bound_log = logger.bind(
            chain_id=self._chain_id,
            block_number=block_number,
        )

        # Cannot finalize until the buffer has at least 2 entries (need
        # a previous block to check parent_hash against).
        if len(self._buffer) < 2:
            bound_log.debug("finality_buffer_filling", buffer_size=len(self._buffer))
            return

        # Verify continuity across the entire confirmation window (ADR-006
        # § Canonical Chain Validation Engine: 'not a single block').
        fork_index = self._find_fork_index()

        if fork_index is None:
            # Continuity holds — advance confirmation lifecycle.
            await self._advance_lifecycle(block_number)
        else:
            # Continuity break detected — reorg handling.
            await self._handle_reorg(fork_index, block)

    def _find_fork_index(self) -> int | None:
        """Check continuity across the buffer. Returns the index of the
        first block whose parent_hash doesn't match the previous block's
        hash, or None if the entire buffer is continuous.

        ADR-006: 'the engine verifies the continuity of the entire
        canonical chain across that confirmation window — not a single
        block.'
        """
        for i in range(1, len(self._buffer)):
            if self._buffer[i].parent_hash != self._buffer[i - 1].hash:
                return i
        return None

    async def _advance_lifecycle(self, current_head: int) -> None:
        """Advance confirmation counts and finalize facts that reached
        depth. Advance checkpoint to the highest finalized block."""
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            await repositories.advance_confirmation_counts(
                session,
                self._chain_id,
                current_head,
                self._confirmation_depth,
            )

        # Checkpoint advances to the highest block that could possibly be
        # finalized: head - depth. ADR-006 § Checkpoint Strategy: 'Only
        # Finalized blocks are eligible for checkpointing.'
        highest_finalizable = current_head - self._confirmation_depth
        if highest_finalizable >= 0:
            now = self._clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            checkpoint = Checkpoint(
                chain_id=self._chain_id,
                last_finalized_block=highest_finalizable,
                last_finalized_at=now,
                updated_at=now,
            )
            async with AsyncSession(self._engine, expire_on_commit=False) as session:
                await repositories.save_checkpoint(session, checkpoint)

            logger.info(
                "finality_advanced",
                chain_id=self._chain_id,
                block_number=current_head,
                confirmations_head=current_head,
                checkpoint_block=highest_finalizable,
            )

    async def _handle_reorg(
        self,
        fork_index: int,
        current_block: BlockMetadata,
    ) -> None:
        """Handle a detected reorg: orphan affected facts, notify handler,
        rebuild buffer.

        ADR-006 § Canonical Chain Validation Engine: 'Chain Reorganization
        Detected → All Affected Facts Marked Orphaned → Replay Canonical
        Chain.'
        """
        fork_block = self._buffer[fork_index - 1]
        fork_block_number = fork_block.number
        # Orphaned range: from the block after the fork point to the end of
        # the old buffer (the blocks we previously thought were canonical).
        orphaned_from = fork_block_number + 1
        orphaned_to = self._buffer[-1].number

        # Mark affected non-finalized facts as ORPHANED (ADR-006 § Orphaned:
        # 'Facts are never deleted. Only their confirmation status changes.').
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            orphaned_count = await repositories.mark_facts_orphaned(
                session, self._chain_id, orphaned_from, orphaned_to
            )

        # Construct ChainReorgEvent (DOC-012 § B.5).
        event = ChainReorgEvent.create(
            chain_id=self._chain_id,
            fork_block_number=fork_block_number,
            orphaned_block_range=(orphaned_from, orphaned_to),
            new_canonical_head_hash=current_block.hash,
            depth=orphaned_to - fork_block_number,
            detected_at=self._clock(),
        )

        # Notify handler (DOC-013 § Exception Hierarchy: reorgs are Domain
        # Events, not exceptions).
        await self._reorg_handler.handle_reorg(event)

        logger.warning(
            "chain_reorg_handled",
            chain_id=self._chain_id,
            fork_block_number=fork_block_number,
            orphaned_from=orphaned_from,
            orphaned_to=orphaned_to,
            orphaned_count=orphaned_count,
            event_id=event.event_id,
        )

        # Clear buffer from the fork point onward — the old chain is no
        # longer canonical. The next on_new_block call will rebuild the
        # buffer from the new canonical chain.
        # fork_index is the index of the first divergent block — remove it
        # and everything after it (the fork block at fork_index-1 is still
        # canonical).
        for _ in range(len(self._buffer) - fork_index):
            self._buffer.pop()

    async def load_checkpoint(self) -> int | None:
        """Load the last finalized block from the checkpoint table. Returns
        None if no checkpoint exists (first run).

        ADR-006 § Recovery Procedure: 'Load checkpoint → Connect to RPC →
        Read current chain head → Determine missing block range → Replay
        missing blocks → Resume live streaming.'
        """
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            checkpoint = await repositories.get_checkpoint(session, self._chain_id)
        if checkpoint is not None:
            logger.info(
                "checkpoint_loaded",
                chain_id=self._chain_id,
                last_finalized_block=checkpoint.last_finalized_block,
            )
            return checkpoint.last_finalized_block
        return None
