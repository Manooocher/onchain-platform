"""Liveness/readiness endpoint (DOC-015 Endpoint Catalog).

Liveness-only (Q3 resolution, option A): reports the process is up + a
version, and does NOT probe Postgres/Redis. A health endpoint that fails
when the DB is down is anti-useful; readiness is a separate `/v1/ready`
later if ever needed.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get(
    "/health",
    summary="Liveness check",
    description="Liveness/readiness. Returns process status and no DB dependency "
    "(liveness-only per DOC-015).",
)
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "onchain_platform_research"}
