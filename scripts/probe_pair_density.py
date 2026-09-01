"""Probe PairCreated event density across candidate block ranges (Phase 0 Step 3).

Uses the production multi-provider pool and the same factory + PAIR_CREATED
topic the collector filters on, to locate block ranges with high pair-creation
density for the historical cohort. Samples `eth_getLogs` over a set of
windows and prints the PairCreated count per window.

This is a short, bounded probe — it must complete within the sandbox's
long-lived-process limit (~4.7 min). Run a handful of ranges per invocation.

Run:
    uv run python scripts/probe_pair_density.py [--start 50000000] [--span 1000000] [--step 500000]
"""

import argparse
import asyncio
from datetime import UTC, datetime

from onchain_platform.acquisition.providers import create_multi_provider
from onchain_platform.platform.config import Settings
from onchain_platform.processing.normalizer import PAIR_CREATED_TOPIC

settings = Settings()


async def _probe_range(provider, start: int, end: int) -> int:
    """Count PairCreated logs from the tracked factory over [start, end].

    Providers cap eth_getLogs at 10,000 blocks per call, so a wide candidate
    range is split into <=10,000-block sub-windows and summed. (The production
    collector is unaffected — process_range queries block-by-block.)
    """
    total = 0
    lo = start
    while lo <= end:
        hi = min(lo + 9_999, end)
        logs = await provider.get_logs(
            from_block=lo,
            to_block=hi,
            address=settings.factory_address,
            topics=[PAIR_CREATED_TOPIC],
        )
        total += len(logs)
        lo = hi + 1
    return total


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=50_000_000)
    parser.add_argument("--span", type=int, default=1_000_000)
    parser.add_argument("--step", type=int, default=500_000)
    parser.add_argument("--budget", type=float, default=180.0)
    args = parser.parse_args()

    p = create_multi_provider("base")
    started = datetime.now(UTC)
    print(f"=== PairCreated density probe (factory {settings.factory_address}) ===")
    print(f"chain={settings.chain_id} span={args.span} step={args.step} budget={args.budget}s\n")

    ranges: list[tuple[int, int]] = []
    base = args.start
    while base < args.start + args.span:
        lo = base
        hi = min(base + args.step - 1, args.start + args.span - 1)
        ranges.append((lo, hi))
        base += args.step

    totals: list[tuple[int, int, int]] = []
    try:
        for lo, hi in ranges:
            elapsed = (datetime.now(UTC) - started).total_seconds()
            if elapsed > args.budget:
                print(f"[budget {args.budget:.0f}s hit — stopping probe]")
                break
            count = await _probe_range(p, lo, hi)
            totals.append((lo, hi, count))
            print(f"  {lo:>9}..{hi:>9}  PairCreated={count}")
    finally:
        await p.close()

    print("\n=== Density summary ===")
    if totals:
        best_lo, best_hi, best_count = max(totals, key=lambda t: t[2])
        print(
            f"highest density: {best_lo}..{best_hi} ({best_count} PairCreated events). "
            "Use this as the cohort range in scripts/cohort_config.py."
        )
        total = sum(t[2] for t in totals)
        print(f"total PairCreated across probed ranges: {total}")


if __name__ == "__main__":
    asyncio.run(main())
