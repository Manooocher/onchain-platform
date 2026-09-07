"""Diagnostic report for ML Foundation data readiness (Issues 1, 3 & 4).

Reads only (never writes). Produces, for the given chain:

- **Issue 1 (zero RUG_PULL positives):** outcome distribution per
  (type, window, label); and a near-miss analysis — for every pair the
  maximum reserve-product drop% over each closed observation window, bucketed
  into >=50/>=70/>=90% to show who came closest to triggering RUG_PULL and
  why (or that the cohort is simply clean).
- **RUG_PULL labeling audit (Phase 1):** the exact engine metric — the
  reserve0×reserve1 product ratio (early-late)/early, as well as the
  individual reserve0 / reserve1 and liquidity_usd drops; distribution of max
  drops; pairs that would trigger at 70/80/90% thresholds; and pairs with a
  physically impossible (>200% one-direction) swing that signals a
  decimal-scale / snapshot artifact — NOT a real reserve change.
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
# A reserve leg swing beyond this magnitude (in either direction) is not a
# plausible 24h change — it flags a decimal-scale / snapshot artifact.
_IMPOSSIBLE_PCT = 200


def _pct(x: Decimal) -> int:
    return int(round(x * _SCALE))


async def _snap_hosts(
    session: AsyncSession,
    entity_id: str,
    eval_t: datetime,
    window: str,
) -> list:
    """PIT snapshots in [eval_t - window, eval_t] for the audit."""
    ws = outcome_engine.parse_observation_window(window)
    return await ts_repos.list_snapshots(
        session, entity_id, eval_t - timedelta(seconds=ws), eval_t + timedelta(seconds=1)
    )


async def audit_rug_pull_logic(session: AsyncSession, chain_id: int) -> dict:
    """Phase 1 — RUG_PULL labeling logic audit.

    Computes, for every pair over every closed observation window:
    - the engine's actual metric: reserve0×reserve1 product ratio
      (early-late)/early — a positive value means reserves FALL (a drop);
      a negative value means reserves RISE (an increase, not a rug).
    - the reserve0 / reserve1 leg drops separately (diagnostic only — the
      engine uses the product, not a single leg).
    - the liquidity_usd drop (only where liquidity_usd is present; 74% of
      exotic pairs are NULL — the engine does NOT use liquidity_usd for the
      rug rule, only reserve-product depth).

    Reports a distribution of max positive drops and which pairs would trigger
    at 50/70/80/90% thresholds, plus impossible-magnitude artifacts (>200%).
    """
    from onchain_platform.persistence.postgres import outcomes_insights as oi

    pairs, _ = await entity_repositories.list_pairs(session, chain_id=chain_id)
    report: dict = {
        "pair_count": len(pairs),
        "pairs_null_liquidity_usd": 0,
        "windows": {},
    }

    for window in ("1h", "24h"):
        w: dict[str, object] = {
            "trigger_ge50": 0,
            "trigger_ge70": 0,
            "trigger_ge80": 0,
            "trigger_ge90": 0,
            "near_misses": [],  # (entity_id, pct)
            "impossible_mag": [],  # (entity_id, leg, pct)
            "worst_product_drop_pct": None,
            "worst_reserve0_drop_pct": None,
            "worst_reserve1_drop_pct": None,
            "worst_liq_usd_drop_pct": None,
            "pairs_evaluated": 0,
            "pairs_product_drop_le0": 0,
        }

        worst_prod = Decimal("-1")
        worst_r0 = Decimal("-1")
        worst_r1 = Decimal("-1")
        worst_liq = Decimal("-1")

        for pair in pairs:
            evals = [
                o.evaluation_timestamp
                for o in await oi.list_outcomes_for_entity(session, pair.canonical_id)
                if o.observation_window == window
            ]
            if not evals:
                continue

            pair_got_eval = False
            mag0 = Decimal("-1")
            mag1 = Decimal("-1")
            has_null_liq = False

            for eval_t in evals:
                snaps = await _snap_hosts(session, pair.canonical_id, eval_t, window)
                if len(snaps) < 2:
                    continue
                snaps.sort(key=lambda s: s.snapshot_timestamp)
                e0 = Decimal(snaps[0].reserve0)
                e1 = Decimal(snaps[0].reserve1)
                l0 = Decimal(snaps[-1].reserve0)
                l1 = Decimal(snaps[-1].reserve1)

                # Engine metric: reserve-product ratio (early-late)/early.
                early_prod = e0 * e1
                if early_prod != 0:
                    dprod = (early_prod - l0 * l1) / early_prod
                    worst_prod = max(worst_prod, dprod)
                    pair_got_eval = True

                # Leg drops (positive = fall).
                if e0 != 0:
                    d0 = (e0 - l0) / e0
                    worst_r0 = max(worst_r0, d0)
                    mag0 = max(mag0, abs(d0))
                if e1 != 0:
                    d1 = (e1 - l1) / e1
                    worst_r1 = max(worst_r1, d1)
                    mag1 = max(mag1, abs(d1))

                # liquidity_usd drop (only where present).
                if snaps[0].liquidity_usd is not None and snaps[-1].liquidity_usd is not None:
                    ei = Decimal(snaps[0].liquidity_usd)
                    if ei != 0:
                        worst_liq = max(worst_liq, (ei - Decimal(snaps[-1].liquidity_usd)) / ei)
                else:
                    has_null_liq = True

            if not pair_got_eval:
                continue
            w["pairs_evaluated"] = int(w["pairs_evaluated"]) + 1
            if has_null_liq:
                report["pairs_null_liquidity_usd"] += 1

            # Impossible-magnitude artifact detection (the engine's own metric
            # is bounded [-1, 1] in the product ratio; an absurd leg magnitude
            # >200% means a decimal-scale/snapshot artifact, not a real change).
            if abs(_pct(mag0)) > _IMPOSSIBLE_PCT:
                w["impossible_mag"].append((pair.canonical_id, "reserve0", _pct(mag0)))
            if abs(_pct(mag1)) > _IMPOSSIBLE_PCT:
                w["impossible_mag"].append((pair.canonical_id, "reserve1", _pct(mag1)))

        w["worst_product_drop_pct"] = _pct(worst_prod) if worst_prod >= Decimal("0") else 0
        w["worst_reserve0_drop_pct"] = _pct(worst_r0) if worst_r0 >= Decimal("0") else 0
        w["worst_reserve1_drop_pct"] = _pct(worst_r1) if worst_r1 >= Decimal("0") else 0
        w["worst_liq_usd_drop_pct"] = _pct(worst_liq) if worst_liq >= Decimal("0") else 0

        # Recompute threshold triggers from the product drops by re-reading the
        # per-pair worst positive product drop (the max of all closed-window
        # evals for the pair). Simple pass: re-open each pair's evals once more.
        # (Keep it readable rather than storing giant per-pair lists.)
        for pair in pairs:
            evals = [
                o.evaluation_timestamp
                for o in await oi.list_outcomes_for_entity(session, pair.canonical_id)
                if o.observation_window == window
            ]
            if not evals:
                continue
            best_drop = Decimal("-1")
            for eval_t in evals:
                snaps = await _snap_hosts(session, pair.canonical_id, eval_t, window)
                if len(snaps) < 2:
                    continue
                snaps.sort(key=lambda s: s.snapshot_timestamp)
                e0 = Decimal(snaps[0].reserve0)
                e1 = Decimal(snaps[0].reserve1)
                l0 = Decimal(snaps[-1].reserve0)
                l1 = Decimal(snaps[-1].reserve1)
                early_prod = e0 * e1
                if early_prod != 0:
                    best_drop = max(best_drop, (early_prod - l0 * l1) / early_prod)
            if best_drop >= Decimal("-0.45"):  # only meaningful near-misses
                p = _pct(best_drop)
                if p >= 50:
                    w["trigger_ge50"] = int(w["trigger_ge50"]) + 1
                if p >= 70:
                    w["trigger_ge70"] = int(w["trigger_ge70"]) + 1
                if p >= 80:
                    w["trigger_ge80"] = int(w["trigger_ge80"]) + 1
                if p >= 90:
                    w["trigger_ge90"] = int(w["trigger_ge90"]) + 1
                if 50 <= p < 90:
                    w["near_misses"].append((pair.canonical_id, p))

        w["pairs_evaluated"] = int(w["pairs_evaluated"])
        report["windows"][window] = w

    return report


async def build_report(engine: AsyncEngine, chain_id: int) -> dict:
    report: dict = {}
    async with AsyncSession(engine, expire_on_commit=False) as session:
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

        # Bar coverage.
        pairs_with_bars = set()
        async with engine.connect() as c:
            r = await c.execute(text("SELECT DISTINCT pair_id FROM market_bars"))
            pairs_with_bars = {row[0] for row in r.fetchall()}

        no_bars: list[str] = []
        no_bars_have_swaps = 0
        no_bars_no_swaps = 0
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


async def main() -> None:
    parser = argparse.ArgumentParser(description="ML data-readiness diagnostics")
    parser.add_argument("--chain", type=int, default=8453)
    parser.add_argument("--dsn", default=os.environ.get("POSTGRES_DSN", _DEFAULT_DSN))
    args = parser.parse_args()
    engine = create_async_engine(args.dsn)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            audit = await audit_rug_pull_logic(session, args.chain)
        rep = await build_report(engine, args.chain)
    finally:
        await engine.dispose()

    print(f"=== Chain {args.chain} — ML data-readiness ===")
    print(f"pairs: {rep['pair_count']}")
    print("\nIssue 1 — outcome distribution (type|window|label):")
    for k, v in rep["outcome_distribution"].items():
        print(f"  {k:40} {v}")

    print("\n=== RUG_PULL labeling audit (Phase 1) ===")
    print(f"pairs with NULL liquidity_usd: {audit['pairs_null_liquidity_usd']}")
    for window in ("1h", "24h"):
        w = audit["windows"][window]
        print(f"\n-- window {window} --")
        print(f"  pairs evaluated: {w['pairs_evaluated']}")
        print(f"  worst product drop%: {w['worst_product_drop_pct']}")
        print(f"  worst reserve0 drop%: {w['worst_reserve0_drop_pct']}")
        print(f"  worst reserve1 drop%: {w['worst_reserve1_drop_pct']}")
        print(f"  worst liquidity_usd drop%: {w['worst_liq_usd_drop_pct']}")
        print(
            f"  product-drop threshold flips: >=50%:{w['trigger_ge50']}  "
            f">=70%:{w['trigger_ge70']}  >=80%:{w['trigger_ge80']}  "
            f">=90%:{w['trigger_ge90']}"
        )
        print(f"  near-misses (50%<=drop<90%): {len(w['near_misses'])}")
        for ent, dpct in list(w["near_misses"])[:10]:
            print(f"    {ent}  drop={dpct}%")
        print(f"  impossible-magnitude artifacts (>200% swing): {len(w['impossible_mag'])}")
        for ent, leg, pct in list(w["impossible_mag"])[:8]:
            print(f"    {ent} {leg} |swing|={pct}%")

    print("\nIssue 4 — bars coverage:")
    print(f"  pairs with bars: {rep['pairs_with_bars']}")
    print(f"  pairs without bars: {rep['pairs_without_bars']}")
    print(f"    of which HAVE swap facts (pipeline gap): {rep['no_bars_have_swap_facts']}")
    print(f"    of which have NO swaps (genuinely inactive): {rep['no_bars_no_swaps']}")


if __name__ == "__main__":
    asyncio.run(main())
