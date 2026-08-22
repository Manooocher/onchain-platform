"""Replay Test: Outcome evaluation is deterministic (Milestone 8 DoD).

Re-processes the FIXED outcome cohort through the LIVE Outcome Engine and
asserts the produced Outcomes are byte-identical across two passes
(ADR-006 Principle 2, DOC-010 § Replay Tests). Outcome.label_value is bool
(not float), so byte-identical assertion is fully valid (DOC-013 §
Determinism Discipline — only float replay is forbidden byte-identical).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

from eth_utils.address import to_checksum_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics import outcome_job
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    PairCreatedPayload,
)
from onchain_platform.domain.schemas.enums import BarInterval, ConfirmationStatus, FactType
from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.postgres import (
    entity_repositories as entity_repos,
)
from onchain_platform.persistence.postgres import (
    repositories as fact_repos,
)
from onchain_platform.persistence.timescale import repositories as ts_repos
from tests.replay.fixtures import outcome_cohort as fx

POOL = to_checksum_address(fx.POOL)
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
TOKEN1 = to_checksum_address("0x" + "22" * 20)
FACT_ID = f"{fx.CHAIN_ID}:0x{'aa' * 32}:0"
ENTITY_ID = pair_canonical_id(fx.CHAIN_ID, POOL)


async def _reset(pg_engine: AsyncEngine, clean_entities: Callable[[], Awaitable[None]]) -> None:
    await clean_entities()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE outcomes, insights, observation_snapshots, market_bars, features")
        )


async def _seed_cohort(pg_engine: AsyncEngine, created: datetime) -> None:
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_repos.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(fx.CHAIN_ID, TOKEN0),
                chain_id=fx.CHAIN_ID,
                contract_address=TOKEN0,
            ),
        )
        await entity_repos.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(fx.CHAIN_ID, TOKEN1),
                chain_id=fx.CHAIN_ID,
                contract_address=TOKEN1,
            ),
        )
        await entity_repos.save_trading_pair(
            session,
            TradingPair(
                canonical_id=ENTITY_ID,
                chain_id=fx.CHAIN_ID,
                dex="uniswap_v2",
                base_token_id=token_canonical_id(fx.CHAIN_ID, TOKEN0),
                quote_token_id=token_canonical_id(fx.CHAIN_ID, TOKEN1),
                pool_address=POOL,
                creation_block=100,
                creation_fact_id=FACT_ID,
            ),
        )
        # FINALIZED PAIR_CREATED fact (Finality Before Analytics).
        await fact_repos.save_fact(
            session,
            BlockchainFact(
                schema_version="1.0",
                fact_id=FACT_ID,
                chain_id=fx.CHAIN_ID,
                fact_type=FactType.PAIR_CREATED,
                block_number=100,
                block_hash="0x" + "11" * 32,
                tx_hash=FACT_ID.split(":")[1],
                log_index=0,
                event_time=created,
                observed_at=created,
                ingested_at=created,
                confirmation_status=ConfirmationStatus.FINALIZED,
                confirmations=10,
                payload=PairCreatedPayload(
                    fact_type="PAIR_CREATED",
                    pair_address=POOL,
                    token0_address=TOKEN0,
                    token1_address=TOKEN1,
                    dex="uniswap_v2",
                ),
            ),
        )
    # Snapshots + bars (hypertables).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for ts, r0, r1 in fx.SNAPSHOTS:
            await ts_repos.save_snapshot(
                session,
                ObservationSnapshot.create(
                    entity_id=ENTITY_ID,
                    chain_id=fx.CHAIN_ID,
                    snapshot_timestamp=ts,
                    observed_at=ts,
                    ingested_at=ts,
                    source="replay",
                    reserve0=r0,
                    reserve1=r1,
                    price="1",
                ),
            )
        for ts, count in fx.BARS:
            await ts_repos.save_bar(
                session,
                MarketBar.create(
                    pair_id=ENTITY_ID,
                    chain_id=fx.CHAIN_ID,
                    interval=BarInterval.ONE_MINUTE,
                    bar_start_time=ts,
                    open_="1",
                    high="1",
                    low="1",
                    close="1",
                    volume_base="0",
                    volume_quote="0",
                    trade_count=count,
                    vwap="1",
                    buy_volume="0",
                    sell_volume="0",
                    source_fact_range=("f", "f"),
                    computed_at=ts,
                ),
            )


async def _run_job(pg_engine: AsyncEngine) -> list[str]:
    """Run the live outcome job with the pinned clock; return serialized
    outcomes (deterministic order)."""
    await outcome_job.run_outcome_evaluation(pg_engine, clock=lambda: fx.CLOCK_NOW)

    from onchain_platform.persistence.postgres.outcomes_insights import list_outcomes_for_entity

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        rows = await list_outcomes_for_entity(session, ENTITY_ID)
    # Deterministic ordering (DOC-013) before serialization.
    rows_sorted = sorted(rows, key=lambda o: (o.outcome_type, o.evaluation_timestamp))
    return [o.model_dump_json() for o in rows_sorted]


async def test_replay_processing_produces_identical_outcomes(
    pg_engine: AsyncEngine, clean_entities: Callable[[], Awaitable[None]]
) -> None:
    """Two passes over the same cohort produce byte-identical Outcomes."""
    # Pass 1.
    await _reset(pg_engine, clean_entities)
    await _seed_cohort(pg_engine, fx.CREATED)
    pass1 = await _run_job(pg_engine)

    assert len(pass1) == 3  # RUG_PULL + SUCCESSFUL_LAUNCH + DEAD_TOKEN
    # The cohort is designed so SUCCESSFUL_LAUNCH fires, others don't.
    from onchain_platform.domain.schemas.outcome import Outcome

    outcomes = [Outcome.model_validate_json(s) for s in pass1]
    label_map = {o.outcome_type: o.label_value for o in outcomes}
    from onchain_platform.domain.schemas.enums import OutcomeType

    assert label_map[OutcomeType.SUCCESSFUL_LAUNCH] is True
    assert label_map[OutcomeType.RUG_PULL] is False
    assert label_map[OutcomeType.DEAD_TOKEN] is False
    # Deterministic evaluation_timestamp = creation + window.
    assert all(o.evaluation_timestamp == fx.EVALUATION_TS for o in outcomes)
    assert all(o.label_definition_version == "1.0" for o in outcomes)

    # Pass 2 — byte-identical (ADR-006 Principle 2).
    pass2 = await _run_job(pg_engine)
    assert pass1 == pass2
