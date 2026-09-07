"""Cohort configuration for Phase 0 Step 3 — historical pair cohort ingestion.

The chosen block range targets a high PairCreated density (determined by
scripts/probe_pair_density.py, or falling back to a recent range if the probe
could not complete within the sandbox time limit). The chunked ingestion
script consumes this config.

This file is committed (config, not runtime state). Runtime progress lives in
scripts/ingestion_state.json (gitignored).
"""

# The tracked Uniswap V2 factory (Settings.factory_address) emits PairCreated
# events; we ingest a bounded historical window to build a real pair cohort
# with closed observation windows for ML Foundation (Phase 4).

COHORT_RANGE = {
    "chain_id": 8453,
    # Phase-1 audit (2026-09-06) found ZERO RUG_PULL positives in the original
    # 50.4M..50.5499M window. To find rug events and grow the cohort, EXPAND
    # ~1M blocks back (49_500_000..50_549_999). **PREPARED but NOT RUN** — the
    # user decides when to execute the expansion on the VPS.
    "start_block": 49_500_000,
    "end_block": 50_549_999,
    # 1000-block chunks are safe on a stable VPS (no ~4.7-min kill), so
    # throughput improves; the ingestion_state.json resume logic is unchanged.
    "chunk_size": 1000,
    "reason": "expand ~1M blocks to find genuine RUG_PULL events (prepared, not run)",
}
