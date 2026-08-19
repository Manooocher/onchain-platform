"""Live smoke test — Milestone 1 DoD item 1, literal reading.

One REAL PairCreated event on Base → one REAL row in blockchain_facts,
correct in every field — run against the live RPC endpoint and the real
Postgres container. Marked `live`: network-dependent, never gates CI.

This is the only test in the suite that reads the wall clock for pipeline
inputs — and it does so by construction: observed_at/ingested_at are
produced by main.py's clock in live mode, which is exactly the DOC-013 §
Determinism Discipline carve-out ("main.py and platform/ ... to produce one
of those three timestamps at the moment data enters the system").
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.acquisition.collector import CollectedLog, Collector
from onchain_platform.acquisition.providers.local_node import LocalNodeProvider
from onchain_platform.domain.schemas.blockchain_fact import PairCreatedPayload
from onchain_platform.persistence.postgres import repositories
from onchain_platform.processing.fact_processor import FactProcessor
from onchain_platform.processing.normalizer import PAIR_CREATED_TOPIC

RPC_URL = "https://mainnet.base.org"
CHAIN_ID = 8453
FACTORY = "0x8909dc15e40173ff4699343b6eb8132c65e18ec6"
DEX = "uniswap_v2"


def _clock() -> datetime:
    return datetime.now(UTC)


@pytest.mark.live
async def test_live_pair_created_becomes_real_row(
    pg_engine: AsyncEngine, clean_facts: Callable[[], Awaitable[None]]
) -> None:
    await clean_facts()
    provider = LocalNodeProvider(RPC_URL)
    try:
        head = await provider.get_chain_head()
        assert await provider.get_chain_id() == CHAIN_ID

        # Find a recent window that actually contains a PairCreated event
        # (the factory emits one roughly every few blocks, but scan rather
        # than assume).
        scan_start = head - 400
        logs = await provider.get_logs(
            from_block=scan_start,
            to_block=head,
            address=FACTORY,
            topics=[PAIR_CREATED_TOPIC],
        )
        assert logs, f"no PairCreated events in blocks {scan_start}..{head}"
        target = logs[0].block_number

        processor = FactProcessor(chain_id=CHAIN_ID, clock=_clock)
        persisted: list[str] = []

        async def handler(collected: CollectedLog) -> None:
            fact = processor.process(collected)
            async with AsyncSession(pg_engine, expire_on_commit=False) as session:
                inserted = await repositories.save_fact(session, fact)
            assert inserted is True
            persisted.append(fact.fact_id)

        collector = Collector(
            provider,
            chain_id=CHAIN_ID,
            factory_address=FACTORY,
            event_topic=PAIR_CREATED_TOPIC,
            dex=DEX,
            handler=handler,
            clock=_clock,
            poll_interval_seconds=0.0,
        )
        await collector.process_range(target, target)
        assert persisted, "expected at least one fact from the target block"

        # The row is real, correct in every field, and matches the raw
        # provider data independently fetched here.
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            row = await repositories.get_fact(session, persisted[0])
        assert row is not None
        raw = next(log for log in logs if log.block_number == target)
        assert row.tx_hash == raw.transaction_hash
        assert row.log_index == raw.log_index
        assert row.block_hash == raw.block_hash
        assert row.chain_id == CHAIN_ID
        assert row.confirmation_status.value == "PENDING"
        payload = row.payload
        assert isinstance(payload, PairCreatedPayload)
        assert payload.dex == DEX
        assert payload.pair_address.startswith("0x") and len(payload.pair_address) == 42

        # And the DB agrees (independent query path, not the ORM).
        async with pg_engine.connect() as conn:
            db_count = (
                await conn.execute(
                    text("SELECT count(*) FROM blockchain_facts WHERE chain_id = :chain_id"),
                    {"chain_id": CHAIN_ID},
                )
            ).scalar_one()
        assert db_count == len(persisted)
    finally:
        await provider.close()
