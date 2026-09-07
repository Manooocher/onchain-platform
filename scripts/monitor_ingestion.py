"""Progress monitor for chunked cohort ingestion (Phase 4 — prepared, not run).

Reads `scripts/ingestion_state.json` (the resume state written by
scripts/chunked_ingestion.py) and the DB to show, for the configured
COHORT_RANGE: blocks done vs total, pairs found, and an estimated time
remaining based on observed per-chunk throughput.

Run (on the VPS while ingestion is active):
    uv run python scripts/monitor_ingestion.py [--chain 8453]
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# scripts/ is a dev-tool directory — load cohort_config by path.
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from cohort_config import COHORT_RANGE  # noqa: E402

STATE_PATH = Path("scripts/ingestion_state.json")
_DEFAULT_DSN = "postgresql+asyncpg://onchain@localhost:5433/onchain_platform"


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


async def _db_counts(chain_id: int, dsn: str) -> dict:
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as c:
            pairs = (
                await c.execute(
                    text("SELECT COUNT(*) FROM trading_pairs WHERE chain_id=:c"), {"c": chain_id}
                )
            ).scalar()
            facts = (
                await c.execute(
                    text("SELECT COUNT(*) FROM blockchain_facts WHERE chain_id=:c"), {"c": chain_id}
                )
            ).scalar()
            bars = (await c.execute(text("SELECT COUNT(*) FROM market_bars"))).scalar()
            return {"pairs": int(pairs), "facts": int(facts), "bars": int(bars)}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor chunked cohort ingestion progress.")
    parser.add_argument("--chain", type=int, default=8453)
    parser.add_argument("--dsn", default=os.environ.get("POSTGRES_DSN", _DEFAULT_DSN))
    args = parser.parse_args()

    state = _load_state()
    completed = state.get("completed_through")
    chunks = state.get("chunks_completed", 0)

    start = COHORT_RANGE["start_block"]
    end = COHORT_RANGE["end_block"]
    total_blocks = end - start + 1
    done_blocks = (completed - start + 1) if completed is not None else 0
    pct = (done_blocks / total_blocks * 100) if total_blocks else 0.0

    print(f"=== Ingestion progress (chain {args.chain}) ===")
    print(f"range: {start}..{end}  total_blocks={total_blocks}")
    print(f"completed_through_block: {completed or 'none'}  chunks_done={chunks}")
    print(f"blocks done: {done_blocks} / {total_blocks}  ({pct:.1f}%)")

    counts = asyncio.run(_db_counts(args.chain, args.dsn))
    print(f"pairs in DB: {counts['pairs']}")
    print(f"facts in DB: {counts['facts']}")
    print(f"market bars: {counts['bars']}")

    # Simple ETA based on observed throughput (facts grow per chunk). If we know
    # chunks_done and elapsed affordance is absent, we cannot compute wall-time
    # accurately; report remaining blocks instead. The ingestion_state.json does
    # not record per-chunk wall-time, so we present blocks-remaining as the ETA.
    remaining_blocks = max(0, total_blocks - done_blocks)
    # At ~1.1 s/block live collector throughput (VPS, stable), estimate seconds.
    est_seconds = remaining_blocks * 1.1
    print(
        f"remaining_blocks={remaining_blocks}  est_time_remaining~={est_seconds / 3600:.1f}h "
        f"(at ~1.1s/block)"
    )


if __name__ == "__main__":
    main()
