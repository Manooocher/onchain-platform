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

import redis.asyncio as redis
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from onchain_platform.acquisition.collector import CollectedLog, Collector, LogFilter
from onchain_platform.acquisition.providers.base import BlockchainProvider
from onchain_platform.acquisition.providers.local_node import LocalNodeProvider
from onchain_platform.analytics import feature_engine, outcome_job, projection_engine
from onchain_platform.analytics import snapshot_job as snapshot_job_mod
from onchain_platform.domain.exceptions import (
    AcquisitionError,
    DomainValidationError,
    PlatformError,
)
from onchain_platform.domain.ids import pair_canonical_id
from onchain_platform.domain.schemas.enums import FactType
from onchain_platform.domain_management import entity_resolution
from onchain_platform.intelligence.intelligence_job import run_intelligence_scan
from onchain_platform.persistence.postgres import entity_repositories as entity_repos
from onchain_platform.persistence.postgres import repositories
from onchain_platform.persistence.timescale import repositories as ts_repos
from onchain_platform.platform.config import Settings, get_chain_id
from onchain_platform.platform.logging import configure_logging
from onchain_platform.platform.scheduler import create_feature_scheduler
from onchain_platform.processing.fact_processor import FactProcessor
from onchain_platform.processing.finality_engine import FinalityEngine
from onchain_platform.processing.normalizer import (
    BURN_TOPIC,
    MINT_TOPIC,
    PAIR_CREATED_TOPIC,
    SWAP_TOPIC,
)
from onchain_platform.processing.reorg_handler import LoggingReorgEventHandler
from onchain_platform.transport import state_cache

logger = structlog.get_logger(__name__)


def _clock() -> datetime:
    """The one sanctioned wall-clock read (DOC-013 § Determinism
    Discipline: main.py produces the Triple Timestamp Standard values at
    the moment data enters the system)."""
    return datetime.now(UTC)


def _build_provider(settings: Settings, chain: str) -> BlockchainProvider:
    """Build the provider for the selected chain.

    When multi-provider config/env keys are present, builds a failover pool
    via create_multi_provider(chain). Otherwise (developer/smoke mode with no
    provider keys) falls back to the plain LocalNodeProvider from RPC_URL, so
    `uv run python -m onchain_platform.main` keeps working out of the box.
    """
    # Keep settings.chain_id consistent with the selected chain on every path.
    settings.chain_id = get_chain_id(chain)
    try:
        from onchain_platform.acquisition.providers import create_multi_provider

        provider = create_multi_provider(chain)
        logger.info("provider_pool_configured", chain=chain)
        return provider
    except AcquisitionError as exc:
        logger.warning(
            "provider_pool_unavailable_falling_back_to_local_node",
            chain=chain,
            error=str(exc),
        )
        return LocalNodeProvider(settings.rpc_url)


async def _run_live(settings: Settings, start_block: int | None, chain: str) -> None:
    """Live ingestion loop: provider → collector → processor → Postgres.

    Milestone 2: wires the FinalityEngine for confirmation lifecycle
    advancement and reorg detection (ADR-006 § Finality Engine).
    """
    engine: AsyncEngine = create_async_engine(settings.postgres_dsn)
    provider = _build_provider(settings, chain)
    processor = FactProcessor(chain_id=settings.chain_id, clock=_clock)

    # Load confirmation depths (ADR-006 § Configurable Confirmation Depth).
    confirmation_depths = settings.load_confirmation_depths()
    chain_depth = confirmation_depths[chain]

    # Construct the reorg handler (DOC-013 § Exception Hierarchy: reorgs
    # are Domain Events, not exceptions). LoggingReorgEventHandler logs at
    # INFO for shallow reorgs, WARNING for deep ones.
    reorg_handler = LoggingReorgEventHandler(confirmation_depth=chain_depth)

    # Construct the Finality Engine (ADR-006 § Finality & Canonical Chain
    # Validation Engine). Dependencies are injected per DOC-013 §
    # Dependency & Composition.
    finality_engine = FinalityEngine(
        chain_id=settings.chain_id,
        confirmation_depth=chain_depth,
        provider=provider,
        engine=engine,
        clock=_clock,
        reorg_handler=reorg_handler,
    )

    # Load checkpoint (ADR-006 § Recovery Procedure: 'Load checkpoint →
    # Connect to RPC → Read current chain head → Determine missing block
    # range → Replay missing blocks → Resume live streaming').
    checkpoint_block = await finality_engine.load_checkpoint()

    # Determine start block. An explicit --start-block (replay/smoke mode)
    # overrides the checkpoint; otherwise resume from checkpoint + 1, or fall
    # back to the chain head for live tailing (ADR-006 § Recovery Procedure).
    if start_block is not None:
        effective_start = start_block
        logger.info(
            "replaying_from_explicit_block",
            chain_id=settings.chain_id,
            start_block=start_block,
        )
    elif checkpoint_block is not None:
        effective_start = checkpoint_block + 1
        logger.info(
            "resuming_from_checkpoint",
            chain_id=settings.chain_id,
            checkpoint_block=checkpoint_block,
            start_block=effective_start,
        )
    else:
        effective_start = None  # will use chain head in run_from

    # Redis client for StateProjection cache (DOC-012 § B.2).
    redis_client = redis.from_url(settings.redis_url)

    # Rebuild state from facts on startup (DOC-006: "State can always be
    # reconstructed by replaying Facts").
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await projection_engine.rebuild_from_facts(session, redis_client, settings.chain_id, _clock)

    async def handler(collected: CollectedLog) -> None:
        fact = processor.process(collected)
        # Session scoped to this call (DOC-013 § Async Conventions).
        # Entity resolution + projection update run in the same session as
        # fact persistence — atomic, no partial state.
        async with AsyncSession(engine, expire_on_commit=False) as session:
            inserted = await repositories.save_fact(session, fact)
            # Eager entity resolution (DOC-004 simplicity principle).
            if fact.fact_type == FactType.PAIR_CREATED:
                await entity_resolution.resolve_from_pair_created(session, fact)
            elif fact.fact_type == FactType.SWAP_EXECUTED:
                await entity_resolution.resolve_from_swap_executed(session, fact)
            # State projection update (DOC-012 § B.2: "continuously
            # recomputed read model").
            await projection_engine.update_projection(session, redis_client, fact, _clock)
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
        filters=[
            LogFilter(
                address=settings.factory_address,
                topic=PAIR_CREATED_TOPIC,
                dex=settings.dex,
            ),
            LogFilter(
                address=None,  # Swap events from any pool
                topic=SWAP_TOPIC,
                dex=settings.dex,
            ),
            LogFilter(
                address=None,  # Mint events from any pool
                topic=MINT_TOPIC,
                dex=settings.dex,
            ),
            LogFilter(
                address=None,  # Burn events from any pool
                topic=BURN_TOPIC,
                dex=settings.dex,
            ),
        ],
        handler=handler,
        clock=_clock,
        poll_interval_seconds=settings.poll_interval_seconds,
        on_block_processed=finality_engine.on_new_block,
    )

    # Graceful shutdown (DOC-013 § Async Conventions): SIGTERM/SIGINT ask
    # the collector to finish the in-flight block, then exit.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, collector.request_stop)

    # Feature computation callback (DOC-010 § Job Scheduling).
    # Defined here in main.py (composition root, exempt from import-linter)
    # so platform/scheduler.py never imports analytics/ directly.
    async def _compute_features() -> None:
        keys = await state_cache.list_state_keys(redis_client)
        now = _clock()
        for key in keys:
            parts = key.split(":")
            if len(parts) != 3:
                continue
            entity_id = pair_canonical_id(settings.chain_id, parts[2])
            async with AsyncSession(engine, expire_on_commit=False) as session:
                pair = await entity_repos.get_trading_pair(session, entity_id)
                if pair is None:
                    continue
                feature_functions = [
                    feature_engine.compute_liquidity_growth_pct_1h,
                    feature_engine.compute_price_momentum_zscore_1h,
                    feature_engine.compute_volume_quote_delta_1h,
                    feature_engine.compute_honeypot_detected_score,
                    feature_engine.compute_liquidity_usd_delta_1h,
                ]
                for fn in feature_functions:
                    feat = await fn(session, entity_id, settings.chain_id, now, now)
                    if feat is not None:
                        await ts_repos.save_feature(session, feat)

    # Start Feature computation scheduler (DOC-010 § Job Scheduling).
    # Hourly interval; max_instances=1 skips if previous job still running.
    scheduler = create_feature_scheduler(compute_fn=_compute_features)

    # Intelligence scan callback (DOC-010 § Job Scheduling).
    # Defined here in main.py (composition root, exempt from contracts).
    async def _run_intelligence() -> None:
        await run_intelligence_scan(engine, redis_client, settings.chain_id, _clock)

    scheduler.add_job(
        _run_intelligence,
        "interval",
        minutes=5,
        id="intelligence_risk_scan",
        name="Intelligence risk scan (GoPlus + risk rules + insights)",
        max_instances=1,
    )

    # Observation snapshot creation callback (TD-3 / M5 gap). Writes a
    # snapshot for every active pair every 5 minutes so historical state
    # queries and feature engineering have real data.
    async def _create_snapshots() -> None:
        count = await snapshot_job_mod.run_snapshot_creation(
            engine,
            redis_client,
            settings.chain_id,
            _clock,
        )
        logger.info("snapshot_job_completed", count=count)

    scheduler.add_job(
        _create_snapshots,
        "interval",
        minutes=5,
        id="snapshot_creation",
        name="Observation snapshot creation",
        max_instances=1,
    )

    # Outcome evaluation callback (DOC-010 § Job Scheduling, Milestone 8).
    # Defined here in main.py (composition root, exempt from contracts) so
    # platform/scheduler.py never imports analytics/ directly.
    async def _evaluate_outcomes() -> None:
        await outcome_job.run_outcome_evaluation(engine, _clock)

    scheduler.add_job(
        _evaluate_outcomes,
        "interval",
        hours=1,
        id="outcome_evaluation",
        name="Outcome evaluation (RUG_PULL / SUCCESSFUL_LAUNCH / DEAD_TOKEN)",
        max_instances=1,
    )

    scheduler.start()
    logger.info("feature_scheduler_started", interval_seconds=3600)
    logger.info("intelligence_scheduler_started", interval_seconds=300)
    logger.info("outcome_scheduler_started", interval_seconds=3600)

    try:
        if effective_start is not None:
            await collector.process_range(effective_start, effective_start)
        else:
            head = await provider.get_chain_head()
            await collector.run_from(head)
    finally:
        scheduler.shutdown(wait=False)
        await provider.close()
        await redis_client.aclose()
        await engine.dispose()


def main() -> None:
    configure_logging()
    settings = Settings()

    parser = argparse.ArgumentParser(
        prog="onchain-platform",
        description="On-chain quant research platform — Milestone 2 with finality engine.",
    )
    parser.add_argument(
        "--chain",
        choices=["base", "ethereum", "bnb"],
        default="base",
        help="Blockchain to collect from (default: base)",
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
        asyncio.run(_run_live(settings, args.start_block, args.chain))
    except (PlatformError, DomainValidationError) as exc:
        # PlatformErrors are the sanctioned boundary shape (DOC-013 §
        # Exception Hierarchy) — log and exit nonzero; a raw traceback here
        # would mean an untranslated infrastructure exception escaped.
        logger.error("fatal_platform_error", error=str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
