"""APScheduler integration for Feature computation (DOC-010 § Job Scheduling).

DOC-010: "APScheduler — Lightweight, in-process job scheduling."
Milestone 6: register Feature computation as a periodic job.

DOC-013 § Dependency & Composition: the scheduler does NOT import any
Capability — main.py wires the actual job functions via callbacks.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

logger = structlog.get_logger(__name__)

# Type alias for the feature computation callback wired by main.py.
# analytics.feature_engine is NOT imported here — that would violate
# the import-linter contract (platform/ must not import analytics/).
ComputeFeaturesFn = Callable[..., Awaitable[None]]


def create_feature_scheduler(
    compute_fn: ComputeFeaturesFn,
    *,
    interval_seconds: int = 3600,
) -> AsyncIOScheduler:
    """Create and configure the APScheduler for Feature computation.

    DOC-010 § Job Scheduling: "Lightweight, in-process job scheduling."
    The actual computation function is passed in from main.py (the
    composition root, exempt from import-linter contracts).
    """
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        compute_fn,
        "interval",
        seconds=interval_seconds,
        id="feature_computation",
        name="Feature computation (liquidity_growth_pct_1h, price_momentum_zscore_1h)",
        max_instances=1,
    )
    return scheduler
