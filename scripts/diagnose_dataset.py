"""Diagnostic report for ML Foundation data readiness (Issues 1 & 4).

Reads only (never writes). Produces, for the given chain:

- **Issue 1 (zero RUG_PULL positives):** outcome distribution per
  (type, window, label); and a **near-miss analysis** — for every pair the
  maximum reserve-product drop% over each observation window, bucketed into
  >=50/>=70/>=90% to show who came closest to triggering RUG_PULL and why
  (or that the cohort is simply clean).
- **Issue 4 (low trading coverage):** how many pairs have market_bars vs not;
  and for pairs WITHOUT bars, whether they have SWAP_EXECUTED facts (a data
  pipeline gap) or none at all (genuinely inactive).

Run on the VPS (where the real 180-pair cohort lives):
    uv run python scripts/diagnose_dataset.py [--chain 8453]
"""

import argparse
import asyncio
import os
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from onchain_platform.analytics import outcome_engine
from onchain_platform.domain.schemas.enums import FactType, OutcomeType
from onchain_platform.persistence.postgres import (
    entity_repositories,
)
from onchain_platform.persistence.postgres import (
    repositories as fact_repos,
)
from onchain_platform.persistence.timescale import repositories as ts_repos

_DEFAULT_DSN = "postgresql+asyncpg://onchain@localhost:5433/onchain_platform"
_SCALE = Decimal(100)  # drop% buckets as integer percent


def _pct(x: Decimal) -> int:
    return int(round(x * _SCALE))


async def _reserve_drop_pct_for_window(
    session: AsyncSession,
    entity_id: str,
    eval_t: datetime,
    window: str,
) -> Decimal | None:
    """PIT reserve-product drop% over an observation window (mirrors the
    RUG_PULL rule math) — used to find near-misses. None if <2 snapshots."""
    window_seconds = outcome_engine.parse_observation_window(window)
    from_time = eval_t - timedelta(seconds=window_seconds)
    to_time = eval_t + timedelta(seconds=1)
    snaps = await ts_repos.list_snapshots(session, entity_id, from_time, to_time)
    if len(snaps) < 2:
        return None
    snaps.sort(key=lambda s: s.snapshot_timestamp)
    early = Decimal(snaps[0].reserve0) * Decimal(snaps[0].reserve1)
    if early == 0:
        return None
    late = Decimal(snaps[-1].reserve0) * Decimal(snaps[-1].reserve1)
    return (early - late) / early


async def build_report(engine: AsyncEngine, chain_id: int) -> dict:
    report: dict = {}
    async with AsyncSession(engine, expire_on_commit=False) as session:
        # ---- Issue 1: outcome distribution ----
        from onchain_platform.persistence.postgres import outcomes_insights as oi

        pairs, _ = await entity_repositories.list_pairs(session, chain_id=chain_id)
        report["pair_count"] = len(pairs)

        dist: dict = {}
        outcome_types = (
            OutcomeType.RUG_PULL,
            OutcomeType.SUCCESSFUL_LAUNCH,
            OutcomeType.DEAD_TOKEN,
        )
        for pair in pairs:
            for ot in outcome_types:
                pair_outcomes = await oi.list_outcomes_for_entity(
                    session, pair.canonical_id, outcome_type=ot
                )
                for o in pair_outcomes:
                    key = (ot.value, o.observation_window, o.label_value)
                    dist[key] = dist.get(key, 0) + 1
        report["outcome_distribution"] = {
            f"{k[0]}|{k[1]}|{'T' if k[2] else 'F'}": v for k, v in dist.items()
        }

        # Near-miss analysis: for each pair, worst drop% per window (from its
        # latest 1h and 24h outcome evaluation timestamps if present, else []).
        near_miss: dict[str, list[int]] = {"1h": [], "24h": []}
        for pair in pairs:
            for window in ("1h", "24h"):
                # Find an outcome of any type for this window to get eval_t;
                # fall back to now-relative proxy is NOT valid (PIT), so only
                # use real closed-window evals we can anchor to.
                evals = [
                    o.evaluation_timestamp
                    for o in await oi.list_outcomes_for_entity(session, pair.canonical_id)
                    if o.observation_window == window
                ]
                for eval_t in evals:
                    drop = await _reserve_drop_pct_for_window(
                        session, pair.canonical_id, eval_t, window
                    )
                    if drop is not None:
                        near_miss[window].append(_pct(drop))
        report["near_miss_max_drop_pct"] = {
            w: (max(v) if v else None) for w, v in near_miss.items()
        }
        # Buckets across all closed-window evaluations.
        buckets = {
            "1h": {"ge50": 0, "ge70": 0, "ge90": 0, "total_evals": 0},
            "24h": {"ge50": 0, "ge70": 0, "ge90": 0, "total_evals": 0},
        }
        for w, vals in near_miss.items():
            for v in vals:
                if v is not None:
                    buckets[w]["total_evals"] += 1
                    if v >= 50:
                        buckets[w]["ge50"] += 1
                    if v >= 70:
                        buckets[w]["ge70"] += 1
                    if v >= 90:
                        buckets[w]["ge90"] += 1
        report["drop_buckets"] = buckets

        # ---- Issue 4: bar coverage ----
        pairs_with_bars = set()
        async with engine.connect() as c:
            r = await c.execute(text("SELECT DISTINCT pair_id FROM market_bars"))
            pairs_with_bars = {row[0] for row in r.fetchall()}

        no_bars: list[str] = []
        no_bars_have_swaps: int = 0
        no_bars_no_swaps: int = 0
        for pair in pairs:
            if pair.canonical_id in pairs_with_bars:
                continue
            no_bars.append(pair.canonical_id)
            swaps, _ = await fact_repos.list_facts_for_pair(
                session,
                chain_id,
                pair.pool_address,
                fact_type=FactType.SWAP_EXECUTED,
                include_unfinalized=False,
                limit=1,
            )
            if swaps:
                no_bars_have_swaps += 1
            else:
                no_bars_no_swaps += 1

        report["pairs_with_bars"] = len(pairs_with_bars)
        report["pairs_without_bars"] = len(no_bars)
        report["no_bars_have_swap_facts"] = no_bars_have_swaps
        report["no_bars_no_swaps"] = no_bars_no_swaps
    return report


def _fmt(buckets: dict) -> str:
    lines = []
    for w in ("1h", "24h"):
        b = buckets[w]
        lines.append(
            f"  {w}: total_evals={b['total_evals']} "
            f"drop>=50%: {b['ge50']}  >=70%: {b['ge70']}  >=90%: {b['ge90']}"
        )
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="ML data-readiness diagnostics")
    parser.add_argument("--chain", type=int, default=8453)
    parser.add_argument("--dsn", default=os.environ.get("POSTGRES_DSN", _DEFAULT_DSN))
    args = parser.parse_args()
    engine = create_async_engine(args.dsn)
    try:
        rep = await build_report(engine, args.chain)
    finally:
        await engine.dispose()

    print(f"=== Chain {args.chain} — ML data-readiness ===")
    print(f"pairs: {rep['pair_count']}")
    print("\nIssue 1 — outcome distribution (type|window|label):")
    for k, v in rep["outcome_distribution"].items():
        print(f"  {k:40} {v}")
    print(f"Issue 1 — near-miss max reserve-drop% per window: {rep['near_miss_max_drop_pct']}")
    print("\ndrop buckets (across closed-window evaluations):")
    print(_fmt(rep["drop_buckets"]))
    print("\nIssue 4 — bars coverage:")
    print(f"  pairs with bars: {rep['pairs_with_bars']}")
    print(f"  pairs without bars: {rep['pairs_without_bars']}")
    print(f"    of which HAVE swap facts (pipeline gap): {rep['no_bars_have_swap_facts']}")
    print(f"    of which have NO swaps (genuinely inactive): {rep['no_bars_no_swaps']}")


if __name__ == "__main__":
    asyncio.run(main())
