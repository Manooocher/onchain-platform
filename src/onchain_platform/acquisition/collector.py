"""Collector — polls a BlockchainProvider for logs and forwards them to the
Fact Processor via direct function call.

Milestone 1 scope (ImplementationPlan § Milestone 1): one fact type
(PAIR_CREATED), one chain (Base), one factory contract, direct function call
from Collector to Fact Processor — NO Redis Streams yet. That is DOC-004's
own principle cited there: simple over sophisticated, optimize after a real
bottleneck appears, not before.

Milestone 2 adds optional finality engine integration: after all facts for
a block are persisted as PENDING, the collector calls
finality_engine.on_new_block() to advance the Confirmation Lifecycle
(ADR-006 § Finality Engine).

Milestone 3 extends the collector to support multiple log filter
configurations (e.g., PairCreated from a factory + Swap from any pool).
Each filter carries its own address/topic/dex label. Logs from all filters
are merged and sorted by (block_number, log_index) for deterministic
ordering (DOC-013 § Determinism Discipline).

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
from onchain_platform.domain.exceptions import AcquisitionError, DomainValidationError

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
    # DEX attribution — from the filter configuration, propagated into the
    # payload by processing/fact_processor.py.
    dex: str


@dataclass(frozen=True)
class LogFilter:
    """One log filter configuration: address + topic + dex label.

    address=None means "any address" (e.g., Swap events from any pool).
    topic is the event signature (keccak256 of the event ABI).
    dex is the label propagated into the CollectedLog.
    """

    address: str | None
    topic: str
    dex: str


class Collector:
    """Polls one chain for logs matching one or more filter configurations
    and forwards them.

    All constructor dependencies are passed in (DOC-013 § Dependency &
    Composition: never imported as configured globals).
    """

    def __init__(
        self,
        provider: BlockchainProvider,
        *,
        chain_id: int,
        filters: list[LogFilter],
        handler: CollectedHandler,
        clock: Callable[[], datetime],
        poll_interval_seconds: float = 2.0,
        on_block_processed: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        self._provider = provider
        self._chain_id = chain_id
        self._filters = filters
        self._handler = handler
        self._clock = clock
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_requested = False
        # Milestone 2: optional callback invoked after each block's facts
        # are persisted. Wired by main.py to finality_engine.on_new_block.
        # When None (Milestone 1 behavior), no post-block processing occurs.
        self._on_block_processed = on_block_processed

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

        # Collect logs from all filter configurations and merge them in
        # deterministic order (DOC-013 § Determinism Discipline: ordered
        # iteration only).
        all_logs: list[tuple[RawLog, str]] = []
        for f in self._filters:
            logs = await self._provider.get_logs(
                from_block=block_number,
                to_block=block_number,
                address=f.address,
                topics=[f.topic],
            )
            for log in logs:
                all_logs.append((log, f.dex))
        # Canonical order: block_number ascending, then log_index.
        all_logs.sort(key=lambda x: (x[0].block_number, x[0].log_index))

        forwarded = 0
        for raw_log, dex in all_logs:
            collected = CollectedLog(
                raw_log=raw_log,
                block=block,
                observed_at=self._clock(),
                dex=dex,
            )
            try:
                await self._handler(collected)
                forwarded += 1
            except DomainValidationError as exc:
                # A single log that cannot be decoded (e.g. a Mint/Burn event
                # missing its indexed sender topic) must not abort an entire
                # block — and certainly not a multi-block chunk or live tail.
                # DOC-013 § Exception Hierarchy / graceful degradation: log it,
                # skip it, continue. Consensus/immutability invariants are
                # untouched; this is an ignored input, not a state change.
                logger.warning(
                    "collected_log_skipped",
                    chain_id=self._chain_id,
                    block_number=block_number,
                    tx_hash=raw_log.transaction_hash,
                    log_index=raw_log.log_index,
                    reason=str(exc),
                )
        # Mandatory structured fields for acquisition/ (DOC-013 §
        # Observability in Code): chain_id, block_number, tx_hash where
        # applicable.
        logger.debug(
            "block_processed",
            chain_id=self._chain_id,
            block_number=block_number,
            tx_hash=(all_logs[0][0].transaction_hash if all_logs else None),
            logs_forwarded=forwarded,
        )
        # Milestone 2: after all facts for this block are persisted as
        # PENDING, invoke the post-block callback (wired by main.py to
        # finality_engine.on_new_block). The callback handles confirmation
        # advancement, reorg detection, and checkpoint writes internally.
        if self._on_block_processed is not None:
            await self._on_block_processed(block_number)
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
