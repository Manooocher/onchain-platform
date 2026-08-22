"""Committed replay fixture: a deterministic outcome-evaluation cohort.

This fixture defines a FIXED online cohort (one pair + its snapshots and
bars with pinned timestamps) that drives the Outcome Engine through the
LIVE pipeline. It is the Milestone 8 analogue of the JSON blockchain
fixtures: a fixed, known input which, when processed twice, must produce
byte-identical Outcomes (DOC-010 § Replay Tests, ADR-006 Principle 2).

The cohort is deliberately chosen so the label outcomes are deterministic:
pair created at T0, healthy reserves throughout, >= 30 trades → the
SUCCESSFUL_LAUNCH outcome fires; no collapse → RUG_PULL false; activity →
DEAD_TOKEN false. Timestamps are pinned in the past relative to the pinned
clock so the 1h observation window is closed.

Import-linter: this is test-only (tests/ is outside the contracts).
"""

from datetime import UTC, datetime, timedelta

CHAIN_ID = 8453
POOL = "0x" + "AB" * 20  # checksummed placeholder used only by test ids
# Pinned, deterministic timestamps (no wall-clock — DOC-013).
CREATED = datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)
OBSERVATION_WINDOW = "1h"
EVALUATION_TS = CREATED + timedelta(hours=1)  # = creation + window
CLOCK_NOW = CREATED + timedelta(hours=3)  # job "now" (injected, not read here)

# Reserve trajectory: no collapse, liquidity survived (late >= 70% of peak).
SNAPSHOTS: list[tuple[datetime, str, str]] = [
    (CREATED, "100", "100"),  # product 10000 (peak)
    (CREATED + timedelta(minutes=30), "95", "100"),  # product 9500
    (EVALUATION_TS, "90", "100"),  # product 9000 >= 0.7*10000
]

# 50 trades across two bars → >= 30 trades => SUCCESSFUL_LAUNCH eligible.
BARS: list[tuple[datetime, int]] = [
    (CREATED, 25),
    (CREATED + timedelta(minutes=1), 25),
]
