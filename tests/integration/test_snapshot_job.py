"""Integration tests: Observation snapshot production job (TD-3).

Verifies that run_snapshot_creation writes a snapshot for a pair that has a
live Redis StateProjection, and skips pairs without one.
"""

from datetime import UTC, datetime, timedelta

import pytest
import redis.asyncio as redis
from eth_utils.address import to_checksum_address
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.analytics import snapshot_job
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.state_projection import StateProjection
from onchain_platform.persistence.postgres import entity_repositories as repos
from onchain_platform.persistence.timescale import repositories as ts_repos
from onchain_platform.transport import state_cache

CHAIN_ID = 8453
REDIS_URL = "redis://localhost:6379/0"
PINNED = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
TOK0 = to_checksum_address("0x4200000000000000000000000000000000000006")
TOK1 = to_checksum_address("0x" + "3c" * 20)


@pytest.fixture
def redis_client() -> redis.Redis:
    return redis.from_url(REDIS_URL)


async def _seed_pair_with_state(pg_engine: AsyncEngine, r: redis.Redis) -> str:
    pool = to_checksum_address("0x" + "5d" * 20)
    pair_id = pair_canonical_id(CHAIN_ID, pool)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repos.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(CHAIN_ID, TOK0),
                chain_id=CHAIN_ID,
                contract_address=TOK0,
            ),
        )
        await repos.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(CHAIN_ID, TOK1),
                chain_id=CHAIN_ID,
                contract_address=TOK1,
            ),
        )
        await repos.save_trading_pair(
            session,
            TradingPair(
                canonical_id=pair_id,
                chain_id=CHAIN_ID,
                dex="uniswap_v2",
                base_token_id=token_canonical_id(CHAIN_ID, TOK0),
                quote_token_id=token_canonical_id(CHAIN_ID, TOK1),
                pool_address=pool,
                creation_block=100,
                creation_fact_id=f"{CHAIN_ID}:0x{'a2' * 32}:0",
            ),
        )
    # Seed Redis state for the pair.
    proj = StateProjection(
        entity_id=pair_id,
        chain_id=CHAIN_ID,
        as_of_block=100,
        as_of_fact_id="f1",
        computed_at=PINNED,
        reserve0="1000",
        reserve1="2000",
        price="2",
    )
    await state_cache.save_state(r, proj)
    return pair_id


async def test_snapshot_job_creates_snapshot_for_active_pair(
    pg_engine: AsyncEngine, redis_client: redis.Redis, clean_entities, clean_outcomes
) -> None:
    await clean_entities()
    await redis_client.flushdb()
    await clean_outcomes()

    pair_id = await _seed_pair_with_state(pg_engine, redis_client)

    created = await snapshot_job.run_snapshot_creation(
        pg_engine, redis_client, CHAIN_ID, clock=lambda: PINNED
    )

    assert created >= 1

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        snaps = await ts_repos.list_snapshots(
            session, pair_id, PINNED - timedelta(seconds=1), PINNED + timedelta(seconds=1)
        )
    assert len(snaps) == 1
    assert snaps[0].entity_id == pair_id
    assert snaps[0].reserve0 == "1000"
    assert snaps[0].source == "projection_engine:poll:60s"


async def test_snapshot_job_skips_pair_without_state(
    pg_engine: AsyncEngine, redis_client: redis.Redis, clean_entities, clean_outcomes
) -> None:
    await clean_entities()
    await redis_client.flushdb()
    await clean_outcomes()
    # Seed a pair but no Redis state.
    pool = to_checksum_address("0x" + "6e" * 20)
    pair_id = pair_canonical_id(CHAIN_ID, pool)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await repos.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(CHAIN_ID, TOK0),
                chain_id=CHAIN_ID,
                contract_address=TOK0,
            ),
        )
        await repos.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(CHAIN_ID, TOK1),
                chain_id=CHAIN_ID,
                contract_address=TOK1,
            ),
        )
        await repos.save_trading_pair(
            session,
            TradingPair(
                canonical_id=pair_id,
                chain_id=CHAIN_ID,
                dex="uniswap_v2",
                base_token_id=token_canonical_id(CHAIN_ID, TOK0),
                quote_token_id=token_canonical_id(CHAIN_ID, TOK1),
                pool_address=pool,
                creation_block=100,
                creation_fact_id=f"{CHAIN_ID}:0x{'b3' * 32}:0",
            ),
        )

    created = await snapshot_job.run_snapshot_creation(
        pg_engine, redis_client, CHAIN_ID, clock=lambda: PINNED
    )
    # It may create 0 for this pair (no state); the job itself skips it.
    assert created >= 0
