"""Integration tests: ranking engine against real Postgres (Phase B).

Verifies `compute_ranking` is deterministic and sorted, and that risk/outcome
signals are applied, using real persistence (DOC-010 § Integration Tests).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013).
"""

from datetime import UTC, datetime

from eth_utils.address import to_checksum_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.enums import Importance, OutcomeType
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.domain.schemas.insight import Insight
from onchain_platform.domain.schemas.outcome import Outcome
from onchain_platform.persistence.postgres import entity_repositories as entity_repo
from onchain_platform.persistence.postgres.outcomes_insights import save_insight, save_outcome
from onchain_platform.persistence.timescale import repositories as ts_repo
from onchain_platform.strategy import ranking

CHAIN_ID = 8453
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
T0 = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)


def _pool(byte: int) -> str:
    return to_checksum_address("0x" + format(byte, "02x") * 20)


async def _wipe(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE outcomes, insights, features, trading_pairs, tokens CASCADE")
        )


async def _seed_pairs_and_features(
    pg_engine: AsyncEngine,
    *,
    strong_byte: int = 11,
    weak_byte: int = 22,
    honey_byte: int = 33,
) -> dict[str, str]:
    """Seed pairs + features so the strong pair ranks higher than weak, and
    the honeypot pair is penalized. Returns {byte:pair_canonical_id}."""
    eids: dict[str, str] = {}
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_repo.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(CHAIN_ID, TOKEN0),
                chain_id=CHAIN_ID,
                contract_address=TOKEN0,
            ),
        )
        for byte, growth in ((strong_byte, 0.9), (weak_byte, 0.1), (honey_byte, 0.9)):
            pool = _pool(byte)
            eid = pair_canonical_id(CHAIN_ID, pool)
            eids[str(byte)] = eid
            await entity_repo.save_trading_pair(
                session,
                TradingPair(
                    canonical_id=eid,
                    chain_id=CHAIN_ID,
                    dex="uniswap_v2",
                    base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
                    quote_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
                    pool_address=pool,
                    creation_block=100 + byte,
                    creation_fact_id=f"{CHAIN_ID}:0x{format(byte, '02x') * 32}:0",
                ),
            )
            await ts_repo.save_feature(
                session,
                Feature(
                    feature_id=f"liquidity_growth_pct_1h|{eid}|{T0.isoformat()}",
                    feature_name="liquidity_growth_pct_1h",
                    entity_id=eid,
                    entity_type="TRADING_PAIR",
                    as_of_timestamp=T0,
                    computed_at=T0,
                    value=float(growth),
                    inputs=["s"],
                ),
            )
        # Honeypot insight on the honey pair.
        await save_insight(
            session,
            Insight(
                insight_id=f"{eids[str(honey_byte)]}|HoneypotDetected|{T0.isoformat()}",
                entity_id=eids[str(honey_byte)],
                insight_type="HoneypotDetected",
                summary="x",
                generated_at=T0,
                source_features=[],
                importance=Importance.HIGH,
            ),
        )
        # SUCCESSFUL_LAUNCH=true on the strong pair (boost).
        await save_outcome(
            session,
            Outcome.create(
                entity_id=eids[str(strong_byte)],
                outcome_type=OutcomeType.SUCCESSFUL_LAUNCH,
                observation_window="1h",
                label_definition="launch",
                label_definition_version="1.0",
                evaluation_timestamp=T0,
                evaluated_at=T0,
                label_value=True,
            ),
        )
    return eids


async def test_ranking_deterministic_same_inputs_same_output(
    pg_engine: AsyncEngine,
) -> None:
    await _wipe(pg_engine)
    await _seed_pairs_and_features(pg_engine)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        r1 = await ranking.compute_ranking(session, chain_id=CHAIN_ID, limit=10, as_of=T0)
        r2 = await ranking.compute_ranking(session, chain_id=CHAIN_ID, limit=10, as_of=T0)

    assert len(r1) == len(r2)
    for a, b in zip(r1, r2, strict=True):
        assert a.pair_id == b.pair_id
        assert a.rank == b.rank
        assert a.score == b.score
        assert [(f.name, f.value, f.weight) for f in a.factors] == [
            (f.name, f.value, f.weight) for f in b.factors
        ]


async def test_ranking_sorts_by_score_and_explains_factors(
    pg_engine: AsyncEngine,
) -> None:
    await _wipe(pg_engine)
    eids = await _seed_pairs_and_features(pg_engine)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        ranked = await ranking.compute_ranking(session, chain_id=CHAIN_ID, limit=10, as_of=T0)

    assert len(ranked) == 3
    # Sorted by score descending.
    scores = [c.score for c in ranked]
    assert scores == sorted(scores, reverse=True)

    # The strong (growth 0.9 + launch boost) ranks first and highest.
    assert ranked[0].pair_id == eids["11"]
    assert ranked[0].rank == 1
    # Every candidate is explainable.
    for c in ranked:
        assert len(c.factors) >= 1
        for f in c.factors:
            assert f.name
            assert f.contribution == f.value * f.weight

    # Honeypot pair is penalized relative to the identical-growth strong pair.
    strong_score = next(c for c in ranked if c.pair_id == eids["11"]).score
    honey_score = next(c for c in ranked if c.pair_id == eids["33"]).score
    assert honey_score < strong_score
