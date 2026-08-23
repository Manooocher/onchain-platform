"""Strategy API router (DOC-009 Strategy, DOC-011 import contracts).

This router is OWNED by the `strategy/` package because research/ may NOT
import strategy/ (DOC-011). It imports `strategy.ranking` (same package) and
`research.api.deps` (allowed — strategy may import research, which is the
lower layer). The composition root (`main.py`) injects the returned router
into `research/api/main.create_app(extra_router=...)`.

The endpoint is deterministic and explains factors (DOC-001); it only
recommends, never acts (DOC-009).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.ranking import RankedCandidate
from onchain_platform.research.api.deps import get_session
from onchain_platform.strategy.ranking import compute_ranking

router = APIRouter()


def build_strategy_router() -> APIRouter:
    """Construct the Strategy router (mounted by the composition root)."""

    @router.get(
        "/strategy/rankings",
        summary="Rank research candidates",
        description="Deterministic, rule-based ranking of research candidates "
        "from Features + risk/outcome signals (DOC-009 Strategy). Returns "
        "candidates sorted by score desc, each with explainable factors.",
        response_model=list[RankedCandidate],
    )
    async def get_rankings(
        chain_id: int | None = Query(default=None, description="Filter by EIP-155 chain id"),
        dex: str | None = Query(default=None, description="Filter by DEX label"),
        limit: int = Query(default=50, ge=1, le=100, description="Max candidates to return"),
        as_of: str | None = Query(default=None, description="ISO-8601 point-in-time"),
        session: AsyncSession = Depends(get_session),
    ) -> list[RankedCandidate]:
        as_of_dt = _parse_as_of(as_of)
        return await compute_ranking(
            session, chain_id=chain_id, dex=dex, limit=limit, as_of=as_of_dt
        )

    return router


def _parse_as_of(as_of: str | None) -> datetime:
    """Parse as_of, defaulting to current server time at the API boundary
    (the strategy engine never reads the wall clock — DOC-013)."""
    if as_of is None:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        # Fall back to now on a malformed as_of (rankings are not PIT-critical
        # enough to 422; keep it robust). Determinism is preserved for a valid
        # input.
        return datetime.now(UTC)
