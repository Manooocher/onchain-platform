"""Monitor data collection progress (Reality Check / ops utility).

Read-only: prints per-table row counts and facts-by-status/type from the real
TimescaleDB. Useful for tracking collection during a Reality Check run.

Run:
    POSTGRES_DSN=postgresql+asyncpg://onchain@localhost:5433/onchain_platform \
        uv run python scripts/monitor_collection.py
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_DEFAULT_DSN = "postgresql+asyncpg://onchain@localhost:5433/onchain_platform"

_FACT_QUERY = text(
    """
    SELECT fact_type, confirmation_status, COUNT(*)
    FROM blockchain_facts
    GROUP BY fact_type, confirmation_status
    ORDER BY fact_type, confirmation_status
    """
)

_ENTITY_QUERY = text(
    """
    SELECT 'trading_pairs', COUNT(*) FROM trading_pairs
    UNION ALL SELECT 'tokens', COUNT(*) FROM tokens
    UNION ALL SELECT 'wallets', COUNT(*) FROM wallets
    """
)

_ANALYTICS_QUERY = text(
    """
    SELECT 'market_bars', COUNT(*) FROM market_bars
    UNION ALL SELECT 'observation_snapshots', COUNT(*) FROM observation_snapshots
    UNION ALL SELECT 'features', COUNT(*) FROM features
    UNION ALL SELECT 'outcomes', COUNT(*) FROM outcomes
    UNION ALL SELECT 'insights', COUNT(*) FROM insights
    """
)


def _print_rows(rows: list) -> None:
    for row in rows:
        if len(row) == 3:
            print(f"{str(row[0]):28} {str(row[1]):15} {str(row[2]):>8}")
        elif len(row) == 2:
            print(f"{str(row[0]):28} {str(row[1]):>8}")
        else:
            print("  ".join(str(c) for c in row))


async def monitor() -> None:
    dsn = os.environ.get("POSTGRES_DSN", _DEFAULT_DSN)
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as conn:
            r = await conn.execute(_FACT_QUERY)
            print("\n=== Facts by Type and Status ===")
            _print_rows(r.fetchall())

            r = await conn.execute(_ENTITY_QUERY)
            print("\n=== Entities ===")
            _print_rows(r.fetchall())

            r = await conn.execute(_ANALYTICS_QUERY)
            print("\n=== Analytics ===")
            _print_rows(r.fetchall())
    finally:
        await engine.dispose()


async def check_providers(chain: str = "base") -> None:
    """Ping each configured provider's eth_blockNumber (genuine reachability).

    Errors (DNS/TLS/4xx) are reported per provider — this is a real check,
    not fabricated health state. Requires provider keys set in the env.
    """
    from onchain_platform.acquisition.providers.factory import create_provider
    from onchain_platform.platform.provider_config import load_provider_config

    config = load_provider_config(chain)
    print(f"\n=== Provider Reachability ({chain}) ===")
    for spec in config.providers:
        provider = create_provider(spec)
        try:
            head = await provider.get_chain_head()
            print(f"{spec.name:8} OK  block={head}")
        except Exception as exc:  # noqa: BLE001 — report any provider error
            print(f"{spec.name:8} ERR {type(exc).__name__}: {str(exc)[:90]}")
        finally:
            await provider.close()


if __name__ == "__main__":
    # `--check-providers` optionally pings the configured RPC pool.
    if "--check-providers" in sys.argv:
        chain = "base"
        asyncio.run(check_providers(chain))
    else:
        asyncio.run(monitor())
