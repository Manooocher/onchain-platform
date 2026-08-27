"""Backfill liquidity_usd + confidence for historical observation snapshots
(TD-1 Phase 5).

For every snapshot lacking a non-null liquidity_usd, classify the pair's pool,
resolve the quote leg via the multi-source oracle, and upsert the snapshot with
liquidity_usd + provenance + confidence (idempotent per snapshot_id).

This is a dev/ops script (DOC-011 scripts/), not a Capability module. It is
honest about two caveats:
- A real ETH provider is required to backfill WETH-denominated pairs; this
  script takes one via the `--eth-provider`-style injection below (default
  none → WETH snapshots stay NULL and are reported as skipped).
- The backfill only updates rows whose liquidity_usd is currently NULL, so it
  is resumable.

Run:
    POSTGRES_DSN=postgresql+asyncpg://onchain@localhost:5433/onchain_platform \
    REDIS_URL=redis://localhost:6379/0 \
    uv run python scripts/backfill_liquidity_usd_with_confidence.py [--batch 1000]
"""

import argparse
import asyncio
import os

import redis.asyncio as redis

from onchain_platform.acquisition.providers.multi_price_oracle import (
    MultiPriceOracle,
)
from onchain_platform.analytics import snapshot_job
from onchain_platform.analytics.pool_classifier import classify_pool

_DEFAULT_DSN = "postgresql+asyncpg://onchain@localhost:5433/onchain_platform"
_DEFAULT_REDIS = "redis://localhost:6379/0"


async def backfill(batch: int = 1000) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from onchain_platform.persistence.postgres import entity_repositories
    from onchain_platform.persistence.timescale import repositories as ts_repos

    dsn = os.environ.get("POSTGRES_DSN", _DEFAULT_DSN)
    redis_url = os.environ.get("REDIS_URL", _DEFAULT_REDIS)
    engine = create_async_engine(dsn)
    r = redis.from_url(redis_url)

    oracle = MultiPriceOracle(r, eth_price_provider=None)

    # Load all pairs so we can classify by pool.
    async with AsyncSession(engine) as session:
        pairs, _ = await entity_repositories.list_pairs(session)
        pair_map = {p.canonical_id: p for p in pairs}

    updated = 0
    skipped = 0
    offset = 0
    while True:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            snaps = await ts_repos.list_snapshots_missing_liquidity_usd(
                session, limit=batch, offset=offset
            )
        if not snaps:
            break

        for snap in snaps:
            pair = pair_map.get(snap.entity_id)
            if pair is None:
                skipped += 1
                continue

            token0 = snapshot_job._token_address(pair.base_token_id)
            token1 = snapshot_job._token_address(pair.quote_token_id)
            pool_class = classify_pool(pair.pool_address, token0, token1)
            quote_type = pool_class.quote_token_type.value

            quote_result = await oracle.get_pool_result(
                (snap.reserve0, snap.reserve1), pool_class, snap.snapshot_timestamp
            )
            liquidity_usd, source, confidence = snapshot_job.liquidity_usd_for_quote(
                (snap.reserve0, snap.reserve1), pool_class, quote_result
            )

            if liquidity_usd is None:
                skipped += 1
                continue

            # Upsert the snapshot with provenance + confidence (idempotent).
            recomputed = snap.model_copy(
                update={
                    "liquidity_usd": liquidity_usd,
                    "liquidity_usd_source": source,
                    "liquidity_usd_confidence": confidence,
                    "quote_token_type": quote_type,
                }
            )
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await ts_repos.save_snapshot(session, recomputed)
            updated += 1

        offset += batch
        print(
            f"backfill: offset={offset}, batch={len(snaps)} (updated={updated}, skipped={skipped})"
        )

    print(f"Done. Updated: {updated}, Skipped: {skipped}, Total scanned: {offset}")
    await r.aclose()
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill liquidity_usd + confidence")
    parser.add_argument("--batch", type=int, default=1000)
    args = parser.parse_args()
    asyncio.run(backfill(batch=args.batch))
