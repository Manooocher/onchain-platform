"""Integration tests: Observation Snapshots against real TimescaleDB.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.persistence.timescale import repositories as ts_repos

CHAIN_ID = 8453
ENTITY_ID = "eip155:8453/pair:0x39f0E675D479088DE08b7f201Ac08e20F899B838"
PINNED = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def _make_snapshot(
    snapshot_timestamp: datetime,
    reserve0: str = "1000",
    reserve1: str = "2000",
    price: str = "2",
) -> ObservationSnapshot:
    return ObservationSnapshot.create(
        entity_id=ENTITY_ID,
        chain_id=CHAIN_ID,
        snapshot_timestamp=snapshot_timestamp,
        observed_at=snapshot_timestamp,
        ingested_at=snapshot_timestamp,
        source="projection_engine:poll:60s",
        reserve0=reserve0,
        reserve1=reserve1,
        price=price,
    )


async def test_snapshot_captures_current_state(pg_engine: AsyncEngine) -> None:
    snap = _make_snapshot(PINNED, reserve0="1500", reserve1="3000", price="2")
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_snapshot(session, snap)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        loaded = await ts_repos.get_latest_snapshot(session, ENTITY_ID)
    assert loaded is not None
    assert loaded.reserve0 == "1500"
    assert loaded.reserve1 == "3000"
    assert loaded.price == "2"


async def test_snapshot_upsert_on_duplicate_timestamp(pg_engine: AsyncEngine) -> None:
    t1 = datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)
    snap1 = _make_snapshot(t1, reserve0="1000", reserve1="2000")
    snap2 = _make_snapshot(t1, reserve0="1500", reserve1="3000")

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_snapshot(session, snap1)
        await ts_repos.save_snapshot(session, snap2)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        loaded = await ts_repos.get_latest_snapshot(session, ENTITY_ID)
    assert loaded is not None
    # Latest wins (upsert on composite key).
    assert loaded.reserve0 == "1500"


async def test_point_in_time_query(pg_engine: AsyncEngine) -> None:
    t1 = datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2024, 4, 22, 13, 0, 0, tzinfo=UTC)
    t3 = datetime(2024, 4, 22, 14, 0, 0, tzinfo=UTC)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await ts_repos.save_snapshot(session, _make_snapshot(t1, reserve0="1000"))
        await ts_repos.save_snapshot(session, _make_snapshot(t2, reserve0="2000"))
        await ts_repos.save_snapshot(session, _make_snapshot(t3, reserve0="3000"))

    # Query for snapshot at T2.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        snapshots = await ts_repos.list_snapshots(session, ENTITY_ID, t1, t3)
    # Should include T1 and T2 (t1 <= ts < t3), not T3.
    assert len(snapshots) == 2
    assert snapshots[0].reserve0 == "1000"
    assert snapshots[1].reserve0 == "2000"
