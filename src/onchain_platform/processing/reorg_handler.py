"""Reorg event handler protocol + logging implementation.

DOC-013 § Exception Hierarchy: 'Reorgs are modeled as Domain Events
published to Redis Streams, never as Exceptions.' M2 creates the typed
contract (ReorgEventHandler protocol) and a logging implementation
(LoggingReorgEventHandler). Future milestones swap in
RedisStreamsReorgEventHandler when transport/event_stream.py arrives.

DOC-013 § Log level policy: 'A successfully-handled ChainReorgDetected
event is logged at INFO or WARNING, never ERROR — it is the Confirmation
Lifecycle working as designed, not a failure of it.'
"""

from typing import Protocol

import structlog

from onchain_platform.domain.schemas.chain_reorg_event import ChainReorgEvent

logger = structlog.get_logger(__name__)


class ReorgEventHandler(Protocol):
    """Protocol for reorg event consumers.

    The Finality Engine calls handle_reorg when a continuity break is
    detected. Implementations decide what to do with the event (log it,
    publish to Redis Streams, alert, etc.).
    """

    async def handle_reorg(self, event: ChainReorgEvent) -> None: ...


class LoggingReorgEventHandler:
    """Logs reorg events via structlog.

    DOC-013 § Observability in Code: mandatory structured fields for
    acquisition/processing are chain_id, block_number (when applicable).
    ChainReorgEvent carries chain_id, fork_block_number, depth.

    DOC-013 § Log level policy: INFO for routine shallow reorgs, WARNING
    for deep reorgs approaching the confirmation depth (the severity
    threshold is depth >= confirmation_depth / 2).
    """

    def __init__(self, confirmation_depth: int) -> None:
        self._severity_threshold = confirmation_depth // 2

    async def handle_reorg(self, event: ChainReorgEvent) -> None:
        level = "warning" if event.depth >= self._severity_threshold else "info"
        log = logger.bind(
            chain_id=event.chain_id,
            fork_block_number=event.fork_block_number,
            depth=event.depth,
            detected_at=event.detected_at.isoformat(),
            orphaned_from=event.orphaned_block_range[0],
            orphaned_to=event.orphaned_block_range[1],
        )
        log.warning if level == "warning" else log.info(
            "chain_reorg_detected",
            event_id=event.event_id,
            new_canonical_head_hash=event.new_canonical_head_hash,
        )
