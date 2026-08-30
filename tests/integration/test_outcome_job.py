"""Integration tests: Outcome evaluation job against real Postgres.

The job scans FINALIZED pairs whose observation window has closed, evaluates
each label type once, and persists idempotently. Tests verify: age-gating,
one-shot idempotency, and "Finality Before Analytics" (pairs whose creating
fact is not FINALIZED are skipped).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from eth_utils.address import to_checksum_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics import outcome_job, outcome_rules
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    PairCreatedPayload,
)
from onchain_platform.domain.schemas.enums import (
    BarInterval,
    ConfirmationStatus,
    FactType,
    OutcomeType,
)
from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.postgres import (
    entity_repositories as entity_repos,
)
from onchain_platform.persistence.postgres import (
    repositories as fact_repos,
)
from onchain_platform.persistence.timescale import repositories as ts_repos

CHAIN_ID = 8453
# Distinct pair address (NOT the shared 0x39f0... entity used by
# test_observation_snapshots/test_state_projection) so this job test never
# pollutes the shared entity and never breaks those unfixtured tests.
POOL = to_checksum_address("0x" + "99" * 20)
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
TOKEN1 = to_checksum_address("0x" + "22" * 20)
CREATED = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
# Clock "now" well past creation + 1h so the 1h window is closed.
NOW = CREATED + timedelta(hours=3)
ENTITY_ID = pair_canonical_id(CHAIN_ID, POOL)


_CleanFn = Callable[[], Awaitable[None]]


def _creation_fact(
    fact_id: str, *, status: ConfirmationStatus, event_time: datetime
) -> BlockchainFact:
    return BlockchainFact(
        schema_version="1.0",
        fact_id=fact_id,
        chain_id=CHAIN_ID,
        fact_type=FactType.PAIR_CREATED,
        block_number=100,
        block_hash="0x" + "11" * 32,
        tx_hash=fact_id.split(":")[1],
        log_index=int(fact_id.split(":")[2]),
        event_time=event_time,
        observed_at=CREATED,
        ingested_at=CREATED,
        confirmation_status=status,
        confirmations=10 if status == ConfirmationStatus.FINALIZED else 0,
        payload=PairCreatedPayload(
            fact_type="PAIR_CREATED",
            pair_address=POOL,
            token0_address=TOKEN0,
            token1_address=TOKEN1,
            dex="uniswap_v2",
        ),
    )


async def _seed_pair(
    pg_engine: AsyncEngine,
    *,
    fact_id: str,
    status: ConfirmationStatus,
    event_time: datetime,
    clean_entities_fn: _CleanFn,
    clean_facts_fn: _CleanFn,
) -> str:
    """Seed Token/TradingPair entities + the creating fact. Returns entity_id."""
    await clean_entities_fn()
    await clean_facts_fn()
    pair_cid = pair_canonical_id(CHAIN_ID, POOL)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        t0 = Token(
            canonical_id=token_canonical_id(CHAIN_ID, TOKEN0),
            chain_id=CHAIN_ID,
            contract_address=TOKEN0,
        )
        t1 = Token(
            canonical_id=token_canonical_id(CHAIN_ID, TOKEN1),
            chain_id=CHAIN_ID,
            contract_address=TOKEN1,
        )
        await entity_repos.save_token(session, t0)
        await entity_repos.save_token(session, t1)
        tp = TradingPair(
            canonical_id=pair_cid,
            chain_id=CHAIN_ID,
            dex="uniswap_v2",
            base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
            quote_token_id=token_canonical_id(CHAIN_ID, TOKEN1),
            pool_address=POOL,
            creation_block=100,
            creation_fact_id=fact_id,
        )
        await entity_repos.save_trading_pair(session, tp)
        await fact_repos.save_fact(
            session, _creation_fact(fact_id, status=status, event_time=event_time)
        )

    return pair_cid


async def _seed_window_data(pg_engine: AsyncEngine) -> None:
    """Snapshots showing a healthy pair + a couple of bars (so RUG_PULL &
    DEAD_TOKEN are False, SUCCESSFUL_LAUNCH needs >= 30 trades though)."""
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        # reserve products: 100*100=10000 at both ends → no collapse.
        await ts_repos.save_snapshot(
            session,
            ObservationSnapshot.create(
                entity_id=ENTITY_ID,
                chain_id=CHAIN_ID,
                snapshot_timestamp=CREATED,
                observed_at=CREATED,
                ingested_at=CREATED,
                source="test",
                reserve0="100",
                reserve1="100",
                price="1",
            ),
        )
        await ts_repos.save_snapshot(
            session,
            ObservationSnapshot.create(
                entity_id=ENTITY_ID,
                chain_id=CHAIN_ID,
                snapshot_timestamp=CREATED + timedelta(minutes=30),
                observed_at=CREATED + timedelta(minutes=30),
                ingested_at=CREATED + timedelta(minutes=30),
                source="test",
                reserve0="100",
                reserve1="100",
                price="1",
            ),
        )
        # 50 trades across two bars → SUCCESSFUL_LAUNCH conditions met.
        for i in range(2):
            await ts_repos.save_bar(
                session,
                MarketBar.create(
                    pair_id=ENTITY_ID,
                    chain_id=CHAIN_ID,
                    interval=BarInterval.ONE_MINUTE,
                    bar_start_time=CREATED + timedelta(minutes=i),
                    open_="1",
                    high="1",
                    low="1",
                    close="1",
                    volume_base="0",
                    volume_quote="0",
                    trade_count=25,
                    vwap="1",
                    buy_volume="0",
                    sell_volume="0",
                    source_fact_range=("f1", "f1"),
                    computed_at=CREATED,
                ),
            )


async def _clear_timescale(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE outcomes, insights, observation_snapshots, market_bars, features")
        )


async def test_job_evaluates_eligible_pair_once(
    pg_engine: AsyncEngine,
    clean_entities: _CleanFn,
    clean_facts: _CleanFn,
) -> None:
    """A FINALIZED pair older than the window is evaluated; SUCCESSFUL_LAUNCH
    fires (>=30 trades, solvent, no honeypot); re-running is idempotent."""
    await _clear_timescale(pg_engine)
    entity_id = await _seed_pair(
        pg_engine,
        fact_id=f"{CHAIN_ID}:0x{'aa' * 32}:0",
        status=ConfirmationStatus.FINALIZED,
        event_time=CREATED,
        clean_entities_fn=clean_entities,
        clean_facts_fn=clean_facts,
    )
    await _seed_window_data(pg_engine)

    p1, c1, r1 = await outcome_job.run_outcome_evaluation(pg_engine, clock=lambda: NOW)
    assert p1 == 1  # one pair evaluated
    assert c1 == 3  # all three outcomes created
    assert r1 == 0

    from onchain_platform.persistence.postgres.outcomes_insights import list_outcomes_for_entity

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        rows = await list_outcomes_for_entity(session, entity_id)
    assert len(rows) == 3
    # SUCCESSFUL_LAUNCH = True (active + solvent), RUG_PULL = False.
    label_map = {r.outcome_type: r.label_value for r in rows}
    assert label_map[OutcomeType.SUCCESSFUL_LAUNCH] is True
    assert label_map[OutcomeType.RUG_PULL] is False
    assert all(r.label_definition_version == "1.0" for r in rows)

    # Second run: all outcomes already exist → nothing new created, all rechecked.
    p2, c2, r2 = await outcome_job.run_outcome_evaluation(pg_engine, clock=lambda: NOW)
    assert c2 == 0
    assert r2 == 3


async def test_job_skips_young_pair_window_not_closed(
    pg_engine: AsyncEngine,
    clean_entities: _CleanFn,
    clean_facts: _CleanFn,
) -> None:
    """A FINALIZED pair younger than the observation window is NOT evaluated."""
    await _clear_timescale(pg_engine)
    # Created 30 minutes before NOW — 1h window not yet closed.
    await _seed_pair(
        pg_engine,
        fact_id=f"{CHAIN_ID}:0x{'bb' * 32}:0",
        status=ConfirmationStatus.FINALIZED,
        event_time=NOW - timedelta(minutes=30),
        clean_entities_fn=clean_entities,
        clean_facts_fn=clean_facts,
    )
    await _seed_window_data(pg_engine)

    from onchain_platform.persistence.postgres.outcomes_insights import list_outcomes_for_entity

    p1, c1, r1 = await outcome_job.run_outcome_evaluation(pg_engine, clock=lambda: NOW)
    assert c1 == 0  # window not closed → no outcomes

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        rows = await list_outcomes_for_entity(session, ENTITY_ID)
    assert len(rows) == 0


async def test_job_skips_pair_with_unfinalized_creation(
    pg_engine: AsyncEngine,
    clean_entities: _CleanFn,
    clean_facts: _CleanFn,
) -> None:
    """A pair whose PAIR_CREATED fact is PENDING is NOT eligible ("Finality
    Before Analytics")."""
    await _clear_timescale(pg_engine)
    await _seed_pair(
        pg_engine,
        fact_id=f"{CHAIN_ID}:0x{'cc' * 32}:0",
        status=ConfirmationStatus.PENDING,  # not finalized
        event_time=CREATED,
        clean_entities_fn=clean_entities,
        clean_facts_fn=clean_facts,
    )
    await _seed_window_data(pg_engine)

    p1, c1, r1 = await outcome_job.run_outcome_evaluation(pg_engine, clock=lambda: NOW)
    assert c1 == 0  # creation fact not FINALIZED → pair not returned
    assert r1 == 0


# ---------------------------------------------------------------------------
# 24h observation window (Phase 0 Step 2)
# ---------------------------------------------------------------------------


async def test_job_creates_6_outcomes_for_24h_eligible_pair(
    pg_engine: AsyncEngine,
    clean_entities: _CleanFn,
    clean_facts: _CleanFn,
) -> None:
    """A pair old enough for BOTH windows gets 3 types × 2 windows = 6 labels,
    each carrying the correct observation_window and window-specific thresholds."""
    await _clear_timescale(pg_engine)
    # Created 25h before the pinned clock → both 1h and 24h windows are closed.
    created_25h = NOW - timedelta(hours=25)
    entity_id = await _seed_pair(
        pg_engine,
        fact_id=f"{CHAIN_ID}:0x{'dd' * 32}:0",
        status=ConfirmationStatus.FINALIZED,
        event_time=created_25h,
        clean_entities_fn=clean_entities,
        clean_facts_fn=clean_facts,
    )
    # 25h of snapshot + bar data (healthy pair).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for ts, r0, r1 in [
            (created_25h, "100", "100"),
            (created_25h + timedelta(minutes=30), "100", "100"),
            (NOW - timedelta(hours=2), "100", "100"),
            (NOW - timedelta(minutes=30), "100", "100"),
        ]:
            await ts_repos.save_snapshot(
                session,
                ObservationSnapshot.create(
                    entity_id=entity_id,
                    chain_id=CHAIN_ID,
                    snapshot_timestamp=ts,
                    observed_at=ts,
                    ingested_at=ts,
                    source="test",
                    reserve0=r0,
                    reserve1=r1,
                    price="1",
                ),
            )
        for i in range(4):
            await ts_repos.save_bar(
                session,
                MarketBar.create(
                    pair_id=entity_id,
                    chain_id=CHAIN_ID,
                    interval=BarInterval.ONE_MINUTE,
                    bar_start_time=created_25h + timedelta(hours=3, minutes=i),
                    open_="1",
                    high="1",
                    low="1",
                    close="1",
                    volume_base="0",
                    volume_quote="0",
                    trade_count=5,
                    vwap="1",
                    buy_volume="0",
                    sell_volume="0",
                    source_fact_range=("f1", "f1"),
                    computed_at=created_25h,
                ),
            )

    p1, c1, r1 = await outcome_job.run_outcome_evaluation(pg_engine, clock=lambda: NOW)

    from onchain_platform.persistence.postgres.outcomes_insights import list_outcomes_for_entity

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        rows = await list_outcomes_for_entity(session, entity_id)
    assert len(rows) == 6  # 3 types × 2 windows

    windows = {r.observation_window for r in rows}
    assert windows == {"1h", "24h"}
    types = {r.outcome_type for r in rows}
    assert types == {OutcomeType.RUG_PULL, OutcomeType.SUCCESSFUL_LAUNCH, OutcomeType.DEAD_TOKEN}

    # Each (type, window) exactly once — the window-aware guard may not skip.
    keys = {(r.outcome_type, r.observation_window) for r in rows}
    assert len(keys) == 6

    # Idempotent: a second run creates nothing and rechecks all six.
    p2, c2, r2 = await outcome_job.run_outcome_evaluation(pg_engine, clock=lambda: NOW)
    assert c2 == 0
    assert r2 == 6


async def test_window_aware_guard_evaluates_24h_even_when_1h_label_exists(
    pg_engine: AsyncEngine,
    clean_entities: _CleanFn,
    clean_facts: _CleanFn,
) -> None:
    """An existing 1h RUG_PULL label must NOT suppress the 24h label. This is
    the critical correctness fix: the one-shot guard keys on
    (entity, type, observation_window), not (entity, type)."""
    await _clear_timescale(pg_engine)
    created_25h = NOW - timedelta(hours=25)
    entity_id = await _seed_pair(
        pg_engine,
        fact_id=f"{CHAIN_ID}:0x{'ee' * 32}:0",
        status=ConfirmationStatus.FINALIZED,
        event_time=created_25h,
        clean_entities_fn=clean_entities,
        clean_facts_fn=clean_facts,
    )
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for ts, r0, r1 in [
            (created_25h, "100", "100"),
            (NOW - timedelta(minutes=30), "100", "100"),
        ]:
            await ts_repos.save_snapshot(
                session,
                ObservationSnapshot.create(
                    entity_id=entity_id,
                    chain_id=CHAIN_ID,
                    snapshot_timestamp=ts,
                    observed_at=ts,
                    ingested_at=ts,
                    source="test",
                    reserve0=r0,
                    reserve1=r1,
                    price="1",
                ),
            )

    # Pre-seed a 1h RUG_PULL outcome so only 24h remains to be created.
    from onchain_platform.domain.schemas.outcome import Outcome
    from onchain_platform.persistence.postgres.outcomes_insights import save_outcome

    eval_1h = created_25h + timedelta(hours=1)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await save_outcome(
            session,
            Outcome.create(
                entity_id=entity_id,
                outcome_type=OutcomeType.RUG_PULL,
                observation_window="1h",
                label_definition=outcome_rules.label_definition_for("RUG_PULL", "1h"),
                label_definition_version=outcome_rules.OUTCOME_RULES_VERSION,
                evaluation_timestamp=eval_1h,
                evaluated_at=NOW,
                label_value=True,
            ),
        )

    p1, c1, r1 = await outcome_job.run_outcome_evaluation(pg_engine, clock=lambda: NOW)

    from onchain_platform.persistence.postgres.outcomes_insights import list_outcomes_for_entity

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        rows = await list_outcomes_for_entity(session, entity_id)

    # The 24h RUG_PULL label must exist (not swallowed by the pre-seeded 1h one).
    rug_24h = [
        r for r in rows if r.observation_window == "24h" and r.outcome_type == OutcomeType.RUG_PULL
    ]
    assert len(rug_24h) == 1, f"24h RUG_PULL was suppressed by the window-blind guard: {rows}"


async def test_rug_pull_threshold_differs_by_window_job(
    pg_engine: AsyncEngine,
    clean_entities: _CleanFn,
    clean_facts: _CleanFn,
) -> None:
    """An ~80% liquidity drop is NOT a 1h rug pull (needs >90%) but IS a 24h
    one (threshold 70%). Verified through the full job for both windows."""
    await _clear_timescale(pg_engine)
    created_25h = NOW - timedelta(hours=25)
    entity_id = await _seed_pair(
        pg_engine,
        fact_id=f"{CHAIN_ID}:0x{'ff' * 32}:0",
        status=ConfirmationStatus.FINALIZED,
        event_time=created_25h,
        clean_entities_fn=clean_entities,
        clean_facts_fn=clean_facts,
    )
    # ~80% liquidity drop between start (100*100) and late (45*45 = 2025).
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_snapshot(
            session,
            ObservationSnapshot.create(
                entity_id=entity_id,
                chain_id=CHAIN_ID,
                snapshot_timestamp=created_25h,
                observed_at=created_25h,
                ingested_at=created_25h,
                source="test",
                reserve0="100",
                reserve1="100",
                price="1",
            ),
        )
        await ts_repos.save_snapshot(
            session,
            ObservationSnapshot.create(
                entity_id=entity_id,
                chain_id=CHAIN_ID,
                snapshot_timestamp=NOW - timedelta(hours=2),
                observed_at=NOW - timedelta(hours=2),
                ingested_at=NOW - timedelta(hours=2),
                source="test",
                reserve0="45",
                reserve1="45",
                price="1",
            ),
        )
        # Enough trades so DEAD_TOKEN stays False in both windows (>= 5 swaps).
        for i in range(3):
            await ts_repos.save_bar(
                session,
                MarketBar.create(
                    pair_id=entity_id,
                    chain_id=CHAIN_ID,
                    interval=BarInterval.ONE_MINUTE,
                    bar_start_time=created_25h + timedelta(hours=5, minutes=i),
                    open_="1",
                    high="1",
                    low="1",
                    close="1",
                    volume_base="0",
                    volume_quote="0",
                    trade_count=5,
                    vwap="1",
                    buy_volume="0",
                    sell_volume="0",
                    source_fact_range=("f1", "f1"),
                    computed_at=created_25h,
                ),
            )

    await outcome_job.run_outcome_evaluation(pg_engine, clock=lambda: NOW)

    from onchain_platform.persistence.postgres.outcomes_insights import list_outcomes_for_entity

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        rows = await list_outcomes_for_entity(session, entity_id)

    rug_by_window = {
        r.observation_window: r.label_value for r in rows if r.outcome_type == OutcomeType.RUG_PULL
    }
    assert rug_by_window.get("1h") is False  # 80% drop < 90% 1h threshold
    assert rug_by_window.get("24h") is True  # 80% drop >= 70% 24h threshold
