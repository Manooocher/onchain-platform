"""Chunked historical ingestion for building a pair cohort (Phase 0 Step 3).

Processes a configured block range in small chunks, resumable across runs.
Each chunk reuses the PRODUCTION ingestion path by invoking
`python -m onchain_platform.main --chain base --start-block X --end-block Y`
(the small composition-root addition lets --start-block bound a range). State
is persisted to scripts/ingestion_state.json so an interrupted run resumes
from the last completed chunk. Fact persistence is already idempotent
(fact_id natural key), so re-running a completed chunk is a no-op.

Sandbox-aware: the environment terminates long-lived processes after ~4.7 min,
so the script runs with a time budget (default 240 s) and stops cleanly well
before that. --one-chunk runs a single chunk for smoke-testing.

Run:
    uv run python scripts/chunked_ingestion.py --one-chunk
    uv run python scripts/chunked_ingestion.py --time-budget 240

Committed. scripts/ingestion_state.json is gitignored runtime state.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from onchain_platform.platform.config import Settings

# scripts/ is a dev-tool directory (not a Python package) — load cohort_config
# by path so this tool stays a plain script (DOC-011: scripts = tooling only).
scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))
from cohort_config import COHORT_RANGE  # noqa: E402

STATE_PATH = Path("scripts/ingestion_state.json")


def _chain_flag(chain_id: int) -> str:
    return {8453: "base", 1: "ethereum", 56: "bnb"}.get(chain_id, "base")


def _state_defaults() -> dict:
    return {
        "chain_id": COHORT_RANGE["chain_id"],
        "completed_through": None,  # last fully-completed inclusive block
        "chunks_completed": 0,
        "total_pairs_created": 0,
        "total_facts_collected": 0,
    }


def load_state() -> dict:
    if not STATE_PATH.exists():
        return _state_defaults()
    try:
        data = json.loads(STATE_PATH.read_text())
        merged = _state_defaults()
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        print("WARNING: ingestion_state.json unreadable; starting fresh.", file=sys.stderr)
        return _state_defaults()


def save_state(state: dict) -> None:
    """Persist state atomically (write temp + rename)."""
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_PATH)


async def _pair_count(engine: AsyncEngine, chain_id: int) -> int:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT COUNT(*) FROM trading_pairs WHERE chain_id = :cid"),
                {"cid": chain_id},
            )
        ).scalar()


async def _fact_count(engine: AsyncEngine, chain_id: int) -> int:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT COUNT(*) FROM blockchain_facts WHERE chain_id = :cid"),
                {"cid": chain_id},
            )
        ).scalar()


def _ingest_chunk(start_block: int, end_block: int, timeout_s: int) -> int:
    """Run one chunk via the production main.py replay path (subprocess).

    Reuses the exact production ingestion path (provider pool → collector →
    processor → finality → persistence) — facts, entity resolution, and
    finality semantics are identical to live collection.
    """
    chain = _chain_flag(COHORT_RANGE["chain_id"])
    cmd = [
        sys.executable,
        "-m",
        "onchain_platform.main",
        "--chain",
        chain,
        "--start-block",
        str(start_block),
        "--end-block",
        str(end_block),
    ]
    print(f"[chunk] {start_block}..{end_block} -> {' '.join(cmd)}", flush=True)
    try:
        return subprocess.run(cmd, env=dict(os.environ), timeout=timeout_s).returncode
    except subprocess.TimeoutExpired:
        print(f"[chunk] {start_block}..{end_block} timed out after {timeout_s}s", file=sys.stderr)
        return -1


async def _run_chunks(*, one_chunk: bool, time_budget: float) -> None:
    settings = Settings()
    engine = create_async_engine(settings.postgres_dsn)

    start = COHORT_RANGE["start_block"]
    configured_end = COHORT_RANGE["end_block"]
    chunk_size = COHORT_RANGE["chunk_size"]
    chain_id = COHORT_RANGE["chain_id"]

    # Never ingest past the live chain head.
    from onchain_platform.acquisition.providers import create_multi_provider

    provider = create_multi_provider("base")
    head = await provider.get_chain_head()
    await provider.close()
    end = min(configured_end, head)
    print(f"chain head={head}; effective ingestion end={end}", flush=True)

    state = load_state()
    completed_through = state["completed_through"]
    if completed_through is not None and completed_through >= end:
        print(f"range already fully ingested through block {completed_through}; nothing to do.")
        return

    next_start = (completed_through + 1) if completed_through is not None else start
    deadline = time.monotonic() + time_budget

    pairs_after: int = 0
    facts_after: int = 0

    while next_start <= end:
        if not one_chunk and time.monotonic() > deadline:
            print(
                f"[budget] time budget {time_budget:.0f}s elapsed — stopping cleanly.",
                flush=True,
            )
            break

        chunk_end = min(next_start + chunk_size - 1, end)
        pairs_before = await _pair_count(engine, chain_id)
        t0 = time.monotonic()

        rc = _ingest_chunk(next_start, chunk_end, timeout_s=int(min(180, time_budget)))
        if rc != 0:
            print(
                f"[chunk] FAILED (exit {rc}) at {next_start}..{chunk_end} — not marked complete.",
                file=sys.stderr,
            )
            break

        elapsed = time.monotonic() - t0
        pairs_after = await _pair_count(engine, chain_id)
        facts_after = await _fact_count(engine, chain_id)
        delta_pairs = max(0, pairs_after - pairs_before)

        state["completed_through"] = chunk_end
        state["chunks_completed"] = state.get("chunks_completed", 0) + 1
        state["total_pairs_created"] = state.get("total_pairs_created", 0) + delta_pairs
        state["total_facts_collected"] = facts_after
        save_state(state)

        print(
            f"[ok] chunk {next_start}..{chunk_end} done in {elapsed:.1f}s "
            f"(delta_pairs={delta_pairs}, pairs={pairs_after}, facts={facts_after})",
            flush=True,
        )
        next_start = chunk_end + 1

        if one_chunk:
            break

    await engine.dispose()
    done_through = state["completed_through"]
    print("\n=== Chunk summary (honest) ===")
    print(f"chunks completed total: {state['chunks_completed']}")
    print(f"ingested through block: {done_through}")
    print(f"pairs in DB: {pairs_after}")
    print(f"facts in DB: {facts_after}")
    print("target: ~200 pairs. If the range is incomplete, later sessions/VM resume it.")
    print(f"state file: {STATE_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunked historical cohort ingestion.")
    mut = parser.add_mutually_exclusive_group(required=True)
    mut.add_argument("--one-chunk", action="store_true", help="Run exactly one chunk then stop.")
    mut.add_argument(
        "--time-budget", type=float, default=None, help="Run until ~this many seconds."
    )
    args = parser.parse_args()

    if args.one_chunk:
        asyncio.run(_run_chunks(one_chunk=True, time_budget=240.0))
    else:
        asyncio.run(_run_chunks(one_chunk=False, time_budget=args.time_budget or 240.0))


if __name__ == "__main__":
    main()
