"""Integration tests: Dataset Builder zero-leakage join (Issue 2) against real
Postgres/TimescaleDB.

Verifies that when building an ML training dataset from features + outcomes,
no feature with `as_of_timestamp > outcome.evaluation_timestamp` is admitted
(lookahead / leakage), and that `assert_zero_leakage` / `leakage_count` behave
correctly.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime, timedelta

import pytest
from eth_utils.address import to_checksum_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.enums import OutcomeType
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.domain.schemas.outcome import Outcome
from onchain_platform.persistence.postgres import (
    entity_repositories as er,
)
from onchain_platform.persistence.postgres import (
    outcomes_insights as oi,
)
from onchain_platform.persistence.timescale import repositories as ts

CHAIN_ID = 8453
POOL = to_checksum_address("0x" + "12" * 20)
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
TOKEN1 = to_checksum_address("0x" + "22" * 20)
ENTITY_ID = pair_canonical_id(CHAIN_ID, POOL)
CREATED = datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)
EVAL_T = CREATED + timedelta(hours=1)  # 1h window closure


async def _seed_pair(session) -> None:
    await er.save_token(
        session,
        Token(
            canonical_id=token_canonical_id(CHAIN_ID, TOKEN0),
            chain_id=CHAIN_ID,
            contract_address=TOKEN0,
        ),
    )
    await er.save_token(
        session,
        Token(
            canonical_id=token_canonical_id(CHAIN_ID, TOKEN1),
            chain_id=CHAIN_ID,
            contract_address=TOKEN1,
        ),
    )
    await er.save_trading_pair(
        session,
        TradingPair(
            canonical_id=ENTITY_ID,
            chain_id=CHAIN_ID,
            dex="uniswap_v2",
            base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
            quote_token_id=token_canonical_id(CHAIN_ID, TOKEN1),
            pool_address=POOL,
            creation_block=100,
            creation_fact_id=f"{CHAIN_ID}:0x{'aa' * 32}:0",
        ),
    )


async def _seed_outcome(session, *, label: bool, eval_t: datetime) -> None:
    await oi.save_outcome(
        session,
        Outcome.create(
            entity_id=ENTITY_ID,
            outcome_type=OutcomeType.RUG_PULL,
            observation_window="1h",
            label_definition="test",
            label_definition_version="1.0",
            evaluation_timestamp=eval_t,
            evaluated_at=eval_t,
            label_value=label,
        ),
    )


async def _clean(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(text("TRUNCATE outcomes, features, trading_pairs, tokens CASCADE"))


async def test_dataset_builder_excludes_post_eval_features(
    pg_engine: AsyncEngine,
) -> None:
    """Issue 2: a feature computed AFTER the outcome's evaluation must not
    appear in the training row (no lookahead / leakage)."""
    from onchain_platform.analytics.dataset_builder import build_training_dataset

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _clean(pg_engine)
        await _seed_pair(session)
        await _seed_outcome(session, label=True, eval_t=EVAL_T)
        # Feature BEFORE evaluation (allowed).
        await ts.save_feature(
            session,
            Feature(
                feature_id=f"liq|{ENTITY_ID}|{(EVAL_T - timedelta(minutes=30)).isoformat()}",
                feature_name="liquidity_growth_pct_1h",
                entity_id=ENTITY_ID,
                entity_type="TRADING_PAIR",
                as_of_timestamp=EVAL_T - timedelta(minutes=30),
                computed_at=EVAL_T - timedelta(minutes=30),
                value=5.0,
                inputs=["s"],
            ),
        )
        # Feature AFTER evaluation (must be EXCLUDED by the PIT join).
        await ts.save_feature(
            session,
            Feature(
                feature_id=f"momentum|{ENTITY_ID}|{(EVAL_T + timedelta(hours=308)).isoformat()}",
                feature_name="price_momentum_zscore_1h",
                entity_id=ENTITY_ID,
                entity_type="TRADING_PAIR",
                as_of_timestamp=EVAL_T + timedelta(hours=308),
                computed_at=EVAL_T + timedelta(hours=308),
                value=9.9,
                inputs=["s"],
            ),
        )

        rows = await build_training_dataset(
            session, chain_id=CHAIN_ID, outcome_type=OutcomeType.RUG_PULL, observation_window="1h"
        )

    assert len(rows) == 1
    row = rows[0]
    # The pre-eval feature is included; the post-eval feature is excluded.
    assert "liquidity_growth_pct_1h" in row.features
    assert "price_momentum_zscore_1h" not in row.features
    # Zero leakage throughout.
    from onchain_platform.analytics.dataset_builder import leakage_count

    assert leakage_count(rows) == 0
    # assert_zero_leakage passes (does not raise).
    from onchain_platform.analytics.dataset_builder import assert_zero_leakage

    assert_zero_leakage(rows)


async def test_dataset_builder_validation_flags_leakage(
    pg_engine: AsyncEngine,
) -> None:
    """Issue 2: assert_zero_leakage raises and leakage_count > 0 when a row
    carries a feature with as_of > outcome evaluation (a join bug)."""
    from onchain_platform.analytics.dataset_builder import (
        FeatureSnapshot,
        TrainingRow,
        assert_zero_leakage,
        leakage_count,
    )

    bad = TrainingRow(
        entity_id=ENTITY_ID,
        outcome_type=OutcomeType.RUG_PULL,
        observation_window="1h",
        label_value=True,
        evaluation_timestamp=EVAL_T,
        features={"momentum": FeatureSnapshot("momentum", 9.9, EVAL_T + timedelta(hours=1))},
    )
    good = TrainingRow(
        entity_id=ENTITY_ID,
        outcome_type=OutcomeType.RUG_PULL,
        observation_window="1h",
        label_value=False,
        evaluation_timestamp=EVAL_T,
        features={"liq": FeatureSnapshot("liq", 1.0, EVAL_T - timedelta(minutes=1))},
    )

    assert leakage_count([bad]) == 1
    assert leakage_count([good]) == 0
    with pytest.raises(AssertionError):
        assert_zero_leakage([bad])
    # No exceptions for the clean row.
    assert_zero_leakage([good])
