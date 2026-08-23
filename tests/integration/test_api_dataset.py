"""Integration tests: dataset assembly endpoint (Phase E).

Verifies the /pairs/{id}/dataset response matches DOC-015's shape, feature
filtering, 90-day cap, required param validation, and 404 for a missing pair.
Runs against real Postgres/TimescaleDB via the app session override.

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
from onchain_platform.domain.schemas.enums import BarInterval, OutcomeType
from onchain_platform.domain.schemas.feature import Feature
from onchain_platform.domain.schemas.market_bar import MarketBar
from onchain_platform.domain.schemas.outcome import Outcome
from onchain_platform.persistence.postgres import entity_repositories as entity_repos
from onchain_platform.persistence.postgres.outcomes_insights import save_outcome
from onchain_platform.persistence.timescale import repositories as ts_repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.main import create_app

CHAIN_ID = 8453
POOL = to_checksum_address("0x" + "88" * 20)
TOKEN0 = to_checksum_address("0x4200000000000000000000000000000000000006")
ENTITY_ID = pair_canonical_id(CHAIN_ID, POOL)
T0 = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
T_END = T0 + timedelta(hours=2)


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
        await conn.execute(
            text("TRUNCATE outcomes, features, market_bars, trading_pairs, tokens CASCADE")
        )


async def _seed(pg_engine: AsyncEngine) -> None:
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
        # Two 1h bars within the range.
        for i in range(2):
            ts = T0 + timedelta(hours=i)
            await ts_repo.save_bar(
                session,
                MarketBar.create(
                    pair_id=ENTITY_ID,
                    chain_id=CHAIN_ID,
                    interval=BarInterval.ONE_HOUR,
                    bar_start_time=ts,
                    open_="1",
                    high="1",
                    low="1",
                    close="1",
                    volume_base="0",
                    volume_quote="0",
                    trade_count=1,
                    vwap="1",
                    buy_volume="0",
                    sell_volume="0",
                    source_fact_range=("f", "f"),
                    computed_at=T0,
                ),
            )
        # Two features.
        for name, val in (("liquidity_growth_pct_1h", 0.05), ("price_momentum_zscore_1h", 1.2)):
            await ts_repo.save_feature(
                session,
                Feature(
                    feature_id=f"{name}|{ENTITY_ID}|{T0.isoformat()}",
                    feature_name=name,
                    entity_id=ENTITY_ID,
                    entity_type="TRADING_PAIR",
                    as_of_timestamp=T0,
                    computed_at=T0,
                    value=val,
                    inputs=["s"],
                ),
            )
        # One outcome.
        await save_outcome(
            session,
            Outcome.create(
                entity_id=ENTITY_ID,
                outcome_type=OutcomeType.SUCCESSFUL_LAUNCH,
                observation_window="1h",
                label_definition="launch",
                label_definition_version="1.0",
                evaluation_timestamp=T0,
                evaluated_at=T0,
                label_value=True,
            ),
        )


async def test_dataset_assembly_matches_doc015_shape(pg_engine: AsyncEngine) -> None:
    await _wipe(pg_engine)
    await _seed(pg_engine)

    qid = quote(ENTITY_ID, safe="")
    async with _make_client(pg_engine) as client:
        resp = await client.get(
            f"/v1/pairs/{qid}/dataset",
            params={"interval": "1h", "start": T0.isoformat(), "end": T_END.isoformat()},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert "pair" in data
        assert "bars" in data
        assert "features" in data
        assert "outcomes" in data
        assert data["pair"]["canonical_id"] == ENTITY_ID
        assert data["bars"]["interval"] == "1h"
        assert isinstance(data["bars"]["items"], list)
        assert len(data["bars"]["items"]) == 2
        assert isinstance(data["features"], list)
        assert len(data["features"]) == 2
        assert isinstance(data["outcomes"], list)
        assert len(data["outcomes"]) == 1


async def test_dataset_feature_names_filter(pg_engine: AsyncEngine) -> None:
    await _wipe(pg_engine)
    await _seed(pg_engine)

    qid = quote(ENTITY_ID, safe="")
    async with _make_client(pg_engine) as client:
        resp = await client.get(
            f"/v1/pairs/{qid}/dataset",
            params={
                "interval": "1h",
                "start": T0.isoformat(),
                "end": T_END.isoformat(),
                "feature_names": "liquidity_growth_pct_1h",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        names = {f["feature_name"] for f in data["features"]}
        assert names == {"liquidity_growth_pct_1h"}


async def test_dataset_90_day_cap_enforced(pg_engine: AsyncEngine) -> None:
    await _wipe(pg_engine)
    await _seed(pg_engine)

    qid = quote(ENTITY_ID, safe="")
    start = "2026-01-01T00:00:00Z"
    end = "2026-06-01T00:00:00Z"  # 151 days
    async with _make_client(pg_engine) as client:
        resp = await client.get(
            f"/v1/pairs/{qid}/dataset",
            params={"interval": "1h", "start": start, "end": end},
        )
        assert resp.status_code == 422


async def test_dataset_missing_required_param_422(pg_engine: AsyncEngine) -> None:
    await _wipe(pg_engine)
    await _seed(pg_engine)

    qid = quote(ENTITY_ID, safe="")
    async with _make_client(pg_engine) as client:
        # Missing interval.
        resp = await client.get(
            f"/v1/pairs/{qid}/dataset",
            params={"start": T0.isoformat(), "end": T_END.isoformat()},
        )
        assert resp.status_code == 422
        # Missing start.
        resp2 = await client.get(
            f"/v1/pairs/{qid}/dataset",
            params={"interval": "1h", "end": T_END.isoformat()},
        )
        assert resp2.status_code == 422


async def test_dataset_pair_not_found_404(pg_engine: AsyncEngine) -> None:
    await _wipe(pg_engine)
    await _seed(pg_engine)

    missing = quote("eip155:8453/pair:0x" + "00" * 20, safe="")
    async with _make_client(pg_engine) as client:
        resp = await client.get(
            f"/v1/pairs/{missing}/dataset",
            params={"interval": "1h", "start": T0.isoformat(), "end": T_END.isoformat()},
        )
        assert resp.status_code == 404
