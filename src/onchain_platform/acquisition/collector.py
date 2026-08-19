"""Collector — polls a BlockchainProvider for logs and forwards them to the
Fact Processor via direct function call.

Milestone 1 scope (ImplementationPlan § Milestone 1): one fact type
(PAIR_CREATED), one chain (Base), one factory contract, direct function call
from Collector to Fact Processor — NO Redis Streams yet. That is DOC-004's
own principle cited there: simple over sophisticated, optimize after a real
bottleneck appears, not before.

Determinism (DOC-013 § Determinism Discipline):
- No wall-clock reads inside this Capability. Time enters only through the
  injected `clock` callable, constructed in main.py — the strictest reading
  of DOC-013 § Determinism Discipline ("main.py and platform/ are the only
  places a clock may be read directly").
- Blocks are processed strictly ascending; logs within a block in log_index
  order (the provider contract guarantees this order — base.py). No set
  iteration anywhere on this path.

Graceful shutdown (DOC-013 § Async Conventions): on stop, the collector
finishes processing whatever block is currently in flight before exiting —
it never leaves a half-processed block behind.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import structlog

from onchain_platform.acquisition.providers.base import (
    BlockchainProvider,
    BlockMetadata,
    RawLog,
)
from onchain_platform.domain.exceptions import AcquisitionError

logger = structlog.get_logger(__name__)

# Type of the downstream handler the collector forwards each collected log
# to. Milestone 1 wires processing.fact_processor here directly
# (ImplementationPlan § Milestone 1 — no Redis Streams yet).
CollectedHandler = Callable[["CollectedLog"], Awaitable[None]]


@dataclass(frozen=True)
class CollectedLog:
    """One log plus everything downstream needs to normalize it.

    Carries only provider-interface primitives (base.py) — never a
    provider-SDK type (DOC-011 § What Does Not Belong Here).
    """

    raw_log: RawLog
    block: BlockMetadata
    # When the RPC/provider emitted this to us — stamped at receipt with the
    # injected clock (DOC-008 Triple Timestamp Standard: observed_at).
    observed_at: datetime
    # DEX attribution of the emitting factory — collector configuration,
    # propagated into the payload by processing/fact_processor.py.
    dex: str


class Collector:
    """Polls one chain for one factory's logs and forwards them.

    All constructor dependencies are passed in (DOC-013 § Dependency &
    Composition: never imported as configured globals).
    """

    def __init__(
        self,
        provider: BlockchainProvider,
        *,
        chain_id: int,
        factory_address: str,
        event_topic: str,
        dex: str,
        handler: CollectedHandler,
        clock: Callable[[], datetime],
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._provider = provider
        self._chain_id = chain_id
        self._factory_address = factory_address
        self._event_topic = event_topic
        self._dex = dex
        self._handler = handler
        self._clock = clock
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_requested = False

    def request_stop(self) -> None:
        """Ask the collector to stop after the in-flight block completes
        (DOC-013 § Async Conventions — graceful shutdown)."""
        self._stop_requested = True

    async def process_range(self, from_block: int, to_block: int) -> int:
        """Process [from_block, to_block] inclusive. Returns the number of
        logs forwarded.

        This is the single processing path for both live ingestion and
        historical replay (ADR-006 § Single Processing Path) — the replay
        fixtures drive exactly this method.
        """
        if to_block < from_block:
            raise AcquisitionError(f"invalid block range: from={from_block} > to={to_block}")
        total = 0
        for block_number in range(from_block, to_block + 1):
            if self._stop_requested:
                # Finish the current block, then stop — never exit mid-block
                # (DOC-013 § Async Conventions).
                logger.info(
                    "collector_stop_requested",
                    chain_id=self._chain_id,
                    block_number=block_number,
                )
                break
            total += await self._process_block(block_number)
        return total

    async def _process_block(self, block_number: int) -> int:
        block = await self._provider.get_block_metadata(block_number)
        logs = await self._provider.get_logs(
            from_block=block_number,
            to_block=block_number,
            address=self._factory_address,
            topics=[self._event_topic],
        )
        forwarded = 0
        for raw_log in logs:  # provider contract: (block_number, log_index) order
            collected = CollectedLog(
                raw_log=raw_log,
                block=block,
                observed_at=self._clock(),
                dex=self._dex,
            )
            await self._handler(collected)
            forwarded += 1
        # Mandatory structured fields for acquisition/ (DOC-013 §
        # Observability in Code): chain_id, block_number, tx_hash where
        # applicable.
        logger.debug(
            "block_processed",
            chain_id=self._chain_id,
            block_number=block_number,
            tx_hash=(logs[0].transaction_hash if logs else None),
            logs_forwarded=forwarded,
        )
        return forwarded

    async def run_from(self, start_block: int) -> None:
        """Live loop: process from start_block to the provider head, then
        poll for new blocks every poll_interval_seconds.

        The poll interval is a fixed asyncio.sleep duration — no wall-clock
        computation (DOC-013 § Determinism Discipline).
        """
        next_block = start_block
        while not self._stop_requested:
            head = await self._provider.get_chain_head()
            if head >= next_block:
                processed_through = await self.process_range(next_block, head)
                logger.info(
                    "ingestion_advanced",
                    chain_id=self._chain_id,
                    block_number=head,
                    logs_forwarded=processed_through,
                )
                next_block = head + 1
            else:
                await asyncio.sleep(self._poll_interval_seconds)
