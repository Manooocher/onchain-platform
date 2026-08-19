"""Composition root — wiring only (DOC-011 § Composition Root).

main.py is the only file allowed to see more than one Capability at once
(AGENTS.md § Where things are; DOC-011): it instantiates the provider,
wires the collector to the fact processor to persistence, and constructs
the ONE Settings instance, session factory, and clock that everything else
receives as parameters (DOC-013 § Dependency & Composition — nothing is
imported as a configured global).

main.py and platform/ are also the only places a wall clock may be read
directly (DOC-013 § Determinism Discipline) — the clock callable below is
the single source of observed_at/ingested_at for the whole process.

No business logic lives here. Anything beyond wiring belongs in a
Capability package.
"""

import argparse
import asyncio
import signal
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from onchain_platform.acquisition.collector import CollectedLog, Collector
from onchain_platform.acquisition.providers.local_node import LocalNodeProvider
from onchain_platform.domain.exceptions import DomainValidationError, PlatformError
from onchain_platform.persistence.postgres import repositories
from onchain_platform.platform.config import Settings
from onchain_platform.platform.logging import configure_logging
from onchain_platform.processing.fact_processor import FactProcessor
from onchain_platform.processing.normalizer import PAIR_CREATED_TOPIC

logger = structlog.get_logger(__name__)


def _clock() -> datetime:
    """The one sanctioned wall-clock read (DOC-013 § Determinism
    Discipline: main.py produces the Triple Timestamp Standard values at
    the moment data enters the system)."""
    return datetime.now(UTC)


async def _run_live(settings: Settings, start_block: int | None) -> None:
    """Live ingestion loop: provider → collector → processor → Postgres."""
    engine: AsyncEngine = create_async_engine(settings.postgres_dsn)
    provider = LocalNodeProvider(settings.rpc_url)
    processor = FactProcessor(chain_id=settings.chain_id, clock=_clock)

    async def handler(collected: CollectedLog) -> None:
        fact = processor.process(collected)
        # Session scoped to this call (DOC-013 § Async Conventions).
        async with AsyncSession(engine, expire_on_commit=False) as session:
            inserted = await repositories.save_fact(session, fact)
        logger.info(
            "fact_persisted",
            chain_id=fact.chain_id,
            block_number=fact.block_number,
            tx_hash=fact.tx_hash,
            fact_id=fact.fact_id,
            inserted=inserted,
        )

    collector = Collector(
        provider,
        chain_id=settings.chain_id,
        factory_address=settings.factory_address,
        event_topic=PAIR_CREATED_TOPIC,
        dex=settings.dex,
        handler=handler,
        clock=_clock,
        poll_interval_seconds=settings.poll_interval_seconds,
    )

    # Graceful shutdown (DOC-013 § Async Conventions): SIGTERM/SIGINT ask
    # the collector to finish the in-flight block, then exit.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, collector.request_stop)

    try:
        if start_block is not None:
            await collector.process_range(start_block, start_block)
        else:
            head = await provider.get_chain_head()
            await collector.run_from(head)
    finally:
        await provider.close()
        await engine.dispose()


def main() -> None:
    configure_logging()
    settings = Settings()

    parser = argparse.ArgumentParser(
        prog="onchain-platform",
        description="On-chain quant research platform — Milestone 1 walking skeleton.",
    )
    parser.add_argument(
        "--start-block",
        type=int,
        default=None,
        help="Process exactly this block number and exit (replay/smoke mode). "
        "Omit to tail the chain head continuously.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run_live(settings, args.start_block))
    except (PlatformError, DomainValidationError) as exc:
        # PlatformErrors are the sanctioned boundary shape (DOC-013 §
        # Exception Hierarchy) — log and exit nonzero; a raw traceback here
        # would mean an untranslated infrastructure exception escaped.
        logger.error("fatal_platform_error", error=str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
