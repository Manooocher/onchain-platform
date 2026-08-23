"""Research Dataset assembly endpoint (DOC-015 § The Research Dataset
Assembly).

`GET /v1/pairs/{id}/dataset?interval=&start=&end=&feature_names=` assembles
pair + bars + features + outcomes in one call. This is the "X (features) →
y (outcome)" shape a researcher/agent needs to start modeling.

- `start`/`end` are required; the server enforces a 90-day max span (422).
- `interval` is required (bars are time-series at a granularity).
- Parallel queries via asyncio.gather on the independent hypertable reads.
- `features` stays a vertical array (never pivoted); `outcomes` is `[]` when
  empty (never omitted); no pagination envelope (the 90-day bound is the size
  control) — all per DOC-015.
"""

import asyncio
from datetime import UTC, datetime
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.schemas.enums import BarInterval
from onchain_platform.persistence.postgres import entity_repositories as entity_repo
from onchain_platform.persistence.postgres.outcomes_insights import list_outcomes_range
from onchain_platform.persistence.timescale import repositories as ts_repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.schemas import DatasetBars, DatasetResponse

router = APIRouter()

MAX_SPAN_DAYS = 90


def _parse_required_dt(value: str, name: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"malformed {name}") from exc


@router.get(
    "/pairs/{pair_id:path}/dataset",
    summary="Assemble a research dataset for a pair",
    description="Assembles pair + bars + features + outcomes for a pair over a "
    "bounded range (DOC-015 § The Research Dataset Assembly). Requires "
    "interval + start + end; enforces a 90-day max span. Features stay a "
    "vertical array; outcomes are [] when empty.",
    response_model=DatasetResponse,
)
async def get_dataset(
    pair_id: str,
    interval: BarInterval = Query(..., description="Bar interval"),
    start: str = Query(..., description="ISO-8601 inclusive start (required)"),
    end: str = Query(..., description="ISO-8601 inclusive end (required)"),
    feature_names: str | None = Query(default=None, description="Comma-separated feature names"),
    session: AsyncSession = Depends(get_session),
) -> DatasetResponse:
    canonical = unquote(pair_id)
    if not canonical.startswith("eip155:"):
        raise HTTPException(status_code=422, detail="pair_id must be a canonical ID")

    start_dt = _parse_required_dt(start, "start")
    end_dt = _parse_required_dt(end, "end")
    if (end_dt - start_dt).days > MAX_SPAN_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"dataset span exceeds {MAX_SPAN_DAYS}-day maximum",
        )

    names = [n.strip() for n in feature_names.split(",")] if feature_names else None

    pair, bars, features, outcomes = await asyncio.gather(
        entity_repo.get_trading_pair(session, canonical),
        ts_repo.list_bars(session, canonical, interval, start_dt, end_dt),
        ts_repo.list_features_range(
            session, canonical, feature_names=names, start=start_dt, end=end_dt
        ),
        list_outcomes_range(session, canonical, start=start_dt, end=end_dt),
    )
    if pair is None:
        raise HTTPException(status_code=404, detail="Trading pair not found")

    return DatasetResponse(
        pair=pair,
        bars=DatasetBars(interval=interval, items=bars),
        features=features,
        outcomes=outcomes,
    )
