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
    # Density probe (scripts/probe_pair_density.py) found 437 PairCreated
    # events across 50_400_000..50_549_999 — a high pair-creation window well
    # above the ~200-pair cohort target. Blocks are comfortably older than 24h
    # relative to the live head, so every ingested pair gets a closed 1h/24h
    # observation window.
    "start_block": 50_400_000,
    "end_block": 50_549_999,
    # 100-block chunks keep each invocation well within the ~4.7 min sandbox
    # long-lived-process limit (real collector throughput ~1.1 s/block).
    "chunk_size": 100,
    "reason": "probe-verified high pair-creation density (437 PairCreated in range)",
}
