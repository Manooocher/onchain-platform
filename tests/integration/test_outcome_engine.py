"""Integration tests: Outcome Engine against real Postgres/TimescaleDB.

Outcome evaluation uses PIT-correct input queries (snapshot/bar timestamps
<= evaluation_timestamp), a deterministic evaluation_timestamp (creation +
window), and reads the honeypot flag from the persisted insights table.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics import outcome_engine, outcome_rules
from onchain_platform.domain.schemas.enums import BarInterval, Importance, OutcomeType
from onchain_platform.domain.schemas.insight import Insight
from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.domain.schemas.outcome import Outcome
from onchain_platform.persistence.postgres.outcomes_insights import save_insight
from onchain_platform.persistence.timescale import repositories as ts_repos

CHAIN_ID = 8453
ENTITY_ID = "eip155:8453/pair:0xabc"
CREATED = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
T_30M = CREATED + timedelta(minutes=30)
# Deterministic evaluation_timestamp = creation + window ("1h").
EVAL_T = CREATED + timedelta(hours=1)
PINNED_CLOCK = datetime(2026, 8, 22, 13, 0, 5, tzinfo=UTC)  # evaluated_at
OBSERVATION_WINDOW = "1h"


def _snap(ts: datetime, reserve0: str, reserve1: str) -> ObservationSnapshot:
    return ObservationSnapshot.create(
        entity_id=ENTITY_ID,
        chain_id=CHAIN_ID,
        snapshot_timestamp=ts,
        observed_at=ts,
        ingested_at=ts,
        source="test",
        reserve0=reserve0,
        reserve1=reserve1,
        price=str(Decimal(reserve1) / Decimal(reserve0)) if Decimal(reserve0) > 0 else "0",
    )


def _bar(ts: datetime, trade_count: int) -> MarketBar:
    return MarketBar.create(
        pair_id=ENTITY_ID,
        chain_id=CHAIN_ID,
        interval=BarInterval.ONE_MINUTE,
        bar_start_time=ts,
        open_="1",
        high="1",
        low="1",
        close="1",
        volume_base="0",
        volume_quote="0",
        trade_count=trade_count,
        vwap="1",
        buy_volume="0",
        sell_volume="0",
        source_fact_range=("f1", "f2"),
        computed_at=EVAL_T,
    )


async def _clear(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE outcomes, insights, observation_snapshots, market_bars, features")
        )


async def _save_snapshots(pg_engine: AsyncEngine, snapshots: list[ObservationSnapshot]) -> None:
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for s in snapshots:
            await ts_repos.save_snapshot(session, s)


async def _save_bar(pg_engine: AsyncEngine, bar: MarketBar) -> None:
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_bar(session, bar)


async def _seed_honeypot(pg_engine: AsyncEngine) -> None:
    ins = Insight(
        insight_id=f"{ENTITY_ID}|HoneypotDetected|{EVAL_T.isoformat()}",
        entity_id=ENTITY_ID,
        insight_type="HoneypotDetected",
        summary="This token appears to be a honeypot — cannot sell.",
        generated_at=EVAL_T,
        source_features=[],
        importance=Importance.HIGH,
    )
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await save_insight(session, ins)


async def _evaluate(pg_engine: AsyncEngine, outcome_type: OutcomeType) -> Outcome | None:
    """Run the engine once with a pinned injected clock (no wall-clock in
    analytics/; DOC-013 § Determinism Discipline)."""
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        result = await outcome_engine.evaluate_outcome(
            session,
            entity_id=ENTITY_ID,
            outcome_type=outcome_type,
            observation_window=OBSERVATION_WINDOW,
            evaluation_timestamp=EVAL_T,
            clock=lambda: PINNED_CLOCK,
        )
    return result


async def test_rug_pull_from_reserve_collapse(pg_engine: AsyncEngine) -> None:
    """Reserve product drops >90% within window → RUG_PULL label."""
    await _clear(pg_engine)
    await _save_snapshots(pg_engine, [_snap(CREATED, "100", "100"), _snap(EVAL_T, "10", "10")])
    await _save_bar(pg_engine, _bar(CREATED, 1))

    outcome = await _evaluate(pg_engine, OutcomeType.RUG_PULL)
    assert outcome is not None
    assert outcome.label_value is True
    assert outcome.label_definition_version == outcome_rules.OUTCOME_RULES_VERSION
    assert outcome.observation_window == "1h"
    assert outcome.evaluation_timestamp == EVAL_T
    assert outcome.outcome_id == f"{ENTITY_ID}|RUG_PULL|{EVAL_T.isoformat()}"
    assert outcome.evaluated_at == PINNED_CLOCK
    # evaluation_timestamp is deterministic (creation + window), not the clock.
    assert outcome.evaluation_timestamp != outcome.evaluated_at


async def test_rug_pull_from_honeypot_insight(pg_engine: AsyncEngine) -> None:
    """A persisted HoneypotDetected insight → RUG_PULL regardless of reserves."""
    await _clear(pg_engine)
    await _seed_honeypot(pg_engine)
    # Reserves did not collapse — honeypot still drives the label (ANY logic).
    await _save_snapshots(pg_engine, [_snap(CREATED, "100", "100"), _snap(EVAL_T, "90", "100")])
    await _save_bar(pg_engine, _bar(CREATED, 1))

    outcome = await _evaluate(pg_engine, OutcomeType.RUG_PULL)
    assert outcome is not None
    assert outcome.label_value is True


async def test_successful_launch_all_conditions(pg_engine: AsyncEngine) -> None:
    """>=30 trades, no honeypot, reserve survived ≥70% → SUCCESSFUL_LAUNCH."""
    await _clear(pg_engine)
    await _save_snapshots(
        pg_engine,
        [
            _snap(CREATED, "100", "100"),
            _snap(T_30M, "95", "100"),  # peak 10000
            _snap(EVAL_T, "90", "100"),  # late 9000 ≥ 0.7*10000
        ],
    )
    await _save_bar(pg_engine, _bar(CREATED, 25))
    await _save_bar(pg_engine, _bar(CREATED + timedelta(minutes=1), 25))  # 50 total

    outcome = await _evaluate(pg_engine, OutcomeType.SUCCESSFUL_LAUNCH)
    assert outcome is not None
    assert outcome.label_value is True


async def test_dead_token_zero_trades(pg_engine: AsyncEngine) -> None:
    """No trades in window → DEAD_TOKEN."""
    await _clear(pg_engine)
    await _save_snapshots(pg_engine, [_snap(CREATED, "100", "100"), _snap(EVAL_T, "100", "100")])

    outcome = await _evaluate(pg_engine, OutcomeType.DEAD_TOKEN)
    assert outcome is not None
    assert outcome.label_value is True


async def test_pit_excludes_post_close_data(pg_engine: AsyncEngine) -> None:
    """Snapshots after evaluation_timestamp must NOT influence the outcome."""
    await _clear(pg_engine)
    await _save_snapshots(pg_engine, [_snap(CREATED, "100", "100"), _snap(EVAL_T, "90", "100")])
    await _save_bar(pg_engine, _bar(CREATED, 1))
    # Post-close (T+2h) collapse MUST NOT be read for the 1h window.
    await _save_snapshots(pg_engine, [_snap(EVAL_T + timedelta(hours=2), "1", "1")])

    outcome = await _evaluate(pg_engine, OutcomeType.RUG_PULL)
    assert outcome is not None
    assert outcome.label_value is False  # collapse happened after the window closed


async def test_insufficient_data_returns_none(pg_engine: AsyncEngine) -> None:
    await _clear(pg_engine)
    outcome = await _evaluate(pg_engine, OutcomeType.RUG_PULL)
    assert outcome is None
