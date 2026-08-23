"""Integration tests: Point-in-Time feature endpoints (Phase D).

Verifies `as_of` resolution, PIT correctness (no lookahead), 404 on missing
features, and default to current server time. Runs against real
Postgres/TimescaleDB via the app session override.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from eth_utils.address import to_checksum_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.persistence.postgres import entity_repositories as entity_repos
from onchain_platform.persistence.timescale import repositories as ts_repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.main import create_app

CHAIN_ID = 8453
POOL = to_checksum_address("0x" + "66" * 20)
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
ENTITY_ID = pair_canonical_id(CHAIN_ID, POOL)
T0 = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=2)
T2 = T0 + timedelta(hours=4)


def _make_client(pg_engine: AsyncEngine) -> httpx.AsyncClient:
    from httpx import ASGITransport

    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _wipe(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(text("TRUNCATE features, trading_pairs, tokens CASCADE"))


async def _seed_features(pg_engine: AsyncEngine) -> None:
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_repos.save_token(
            session,
            Token(
                canonical_id=token_canonical_id(CHAIN_ID, TOKEN0),
                chain_id=CHAIN_ID,
                contract_address=TOKEN0,
            ),
        )
        await entity_repos.save_trading_pair(
            session,
            TradingPair(
                canonical_id=ENTITY_ID,
                chain_id=CHAIN_ID,
                dex="uniswap_v2",
                base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
                quote_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
                pool_address=POOL,
                creation_block=100,
                creation_fact_id=f"{CHAIN_ID}:0x{'aa' * 32}:0",
            ),
        )
        # liquidity_growth at T0 and T1 (earlier + later).
        for ts, val in ((T0, 10.0), (T1, 25.0)):
            await ts_repo.save_feature(
                session,
                Feature(
                    feature_id=f"liquidity_growth_pct_1h|{ENTITY_ID}|{ts.isoformat()}",
                    feature_name="liquidity_growth_pct_1h",
                    entity_id=ENTITY_ID,
                    entity_type="TRADING_PAIR",
                    as_of_timestamp=ts,
                    computed_at=ts,
                    value=val,
                    inputs=["s"],
                ),
            )
        # A feature only created AFTER T2 (lookahead exclusion).
        await ts_repo.save_feature(
            session,
            Feature(
                feature_id=f"future_only_pct|{ENTITY_ID}|{T2.isoformat()}",
                feature_name="future_only_pct",
                entity_id=ENTITY_ID,
                entity_type="TRADING_PAIR",
                as_of_timestamp=T2,
                computed_at=T2,
                value=99.0,
                inputs=["s"],
            ),
        )


async def test_pit_single_feature_returns_version_at_as_of(pg_engine: AsyncEngine) -> None:
    await _wipe(pg_engine)
    await _seed_features(pg_engine)

    qid = quote(ENTITY_ID, safe="")
    async with _make_client(pg_engine) as client:
        # At T1 (after T0, <= T1) → the T1 version (25).
        resp = await client.get(
            f"/v1/entities/{qid}/features/liquidity_growth_pct_1h",
            params={"as_of": T1.isoformat()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["feature_name"] == "liquidity_growth_pct_1h"
        # Feature.as_of_timestamp serializes with the +00:00 offset (pydantic
        # serializer), so compare against the ISO form after stripping 'Z'.
        assert data["as_of_timestamp"].replace("Z", "+00:00") == T1.isoformat()
        assert data["value"] == 25.0

        # At T0 + 1h → the T0 version (10), not the T1 one.
        mid = (T0 + timedelta(hours=1)).isoformat()
        resp2 = await client.get(
            f"/v1/entities/{qid}/features/liquidity_growth_pct_1h",
            params={"as_of": mid},
        )
        assert resp2.status_code == 200
        assert resp2.json()["value"] == 10.0


async def test_pit_lookahead_excluded(pg_engine: AsyncEngine) -> None:
    """A feature created after as_of must be excluded (404 for that name)."""
    await _wipe(pg_engine)
    await _seed_features(pg_engine)

    qid = quote(ENTITY_ID, safe="")
    async with _make_client(pg_engine) as client:
        # future_only_pct only exists at/as T2; query at T0 → 404.
        resp = await client.get(
            f"/v1/entities/{qid}/features/future_only_pct",
            params={"as_of": T0.isoformat()},
        )
        assert resp.status_code == 404


async def test_pit_feature_not_found_404(pg_engine: AsyncEngine) -> None:
    await _wipe(pg_engine)
    await _seed_features(pg_engine)

    qid = quote(ENTITY_ID, safe="")
    async with _make_client(pg_engine) as client:
        resp = await client.get(
            f"/v1/entities/{qid}/features/nonexistent_pct",
            params={"as_of": "2026-01-01T00:00:00Z"},
        )
        assert resp.status_code == 404
        assert "error" in resp.json()


async def test_pit_all_features_latest_per_name(pg_engine: AsyncEngine) -> None:
    await _wipe(pg_engine)
    await _seed_features(pg_engine)

    qid = quote(ENTITY_ID, safe="")
    async with _make_client(pg_engine) as client:
        resp = await client.get(f"/v1/entities/{qid}/features", params={"as_of": T2.isoformat()})
        assert resp.status_code == 200
        data = resp.json()
        names = {item["feature_name"] for item in data["items"]}
        assert names == {"liquidity_growth_pct_1h", "future_only_pct"}
        lg = next(i for i in data["items"] if i["feature_name"] == "liquidity_growth_pct_1h")
        assert lg["value"] == 25.0


async def test_pit_defaults_to_now(pg_engine: AsyncEngine) -> None:
    """With no as_of, resolve to current server time (returns a value)."""
    await _wipe(pg_engine)
    await _seed_features(pg_engine)

    qid = quote(ENTITY_ID, safe="")
    async with _make_client(pg_engine) as client:
        resp = await client.get(f"/v1/entities/{qid}/features/liquidity_growth_pct_1h")
        assert resp.status_code == 200
