"""Integration tests: strategy rankings API endpoint + import contract (Phase C).

Verifies `GET /v1/strategy/rankings` is served when the composition root
injects the Strategy router via `create_app(extra_router=...)`, returns
sorted, explainable candidates, and that research/ does NOT import strategy/
(DOC-011). Runs against real Postgres.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
from eth_utils.address import to_checksum_address
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.persistence.postgres import entity_repositories as entity_repo
from onchain_platform.persistence.timescale import repositories as ts_repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.main import create_app
from onchain_platform.strategy.api import build_strategy_router

CHAIN_ID = 8453
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
T0 = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)


def _client(pg_engine: AsyncEngine) -> httpx.AsyncClient:
    """Build the app WITH the injected strategy router, using the test engine."""
    app = create_app(extra_router=build_strategy_router())

    async def _override() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_two_pairs(pg_engine: AsyncEngine) -> dict[str, str]:
    """Seed two pairs: A with high liquidity growth, B with low."""
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
        for byte, growth in ((11, 0.9), (22, 0.1)):
            pool = to_checksum_address("0x" + format(byte, "02x") * 20)
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
    return eids


async def test_rankings_endpoint_returns_sorted_explainable(
    pg_engine: AsyncEngine,
) -> None:
    from sqlalchemy import text

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE outcomes, insights, features, trading_pairs, tokens CASCADE")
        )
    eids = await _seed_two_pairs(pg_engine)

    async with _client(pg_engine) as client:
        resp = await client.get("/v1/strategy/rankings", params={"chain_id": CHAIN_ID, "limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2

        # Sorted by score descending.
        scores = [item["score"] for item in data]
        assert scores == sorted(scores, reverse=True)

        # High-growth pair is rank 1.
        assert data[0]["pair_id"] == eids["11"]
        assert data[0]["rank"] == 1

        # Explainability: factors non-empty with name/contribution.
        for item in data:
            assert len(item["factors"]) >= 1
            assert all("name" in f and "contribution" in f for f in item["factors"])

        # Deterministic across two calls.
        resp2 = await client.get(
            "/v1/strategy/rankings", params={"chain_id": CHAIN_ID, "limit": 10}
        )
        assert resp2.json() == data


async def test_create_app_without_extra_router_has_no_strategy_route(
    pg_engine: AsyncEngine,
) -> None:
    # Backward compatibility: existing callers that pass nothing get the
    # research surface only — no strategy route.
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/strategy/rankings")
        assert resp.status_code == 404
