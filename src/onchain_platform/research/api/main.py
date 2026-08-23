"""Research API — FastAPI app factory (DOC-011 research/api/, DOC-015).

Wiring only: CORS (localhost:8501, GET-only), correlation_id + error
middleware, the `/v1/`-prefixed router set, and the `/v1/openapi.json`
serving of the generated OpenAPI document (DOC-015 § OpenAPI).

DOC-011: `research/` may import analytics, intelligence, domain and
cross-cutting persistence/transport/platform — never acquisition,
processing, domain_management, or strategy. This file stays wiring-only.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from onchain_platform.domain.exceptions import PlatformError
from onchain_platform.research.api.errors import (
    add_correlation_id_middleware,
    http_exception_handler,
    platform_error_handler,
)

V1_PREFIX = "/v1"


def create_app() -> FastAPI:
    """Construct the Research Platform FastAPI application."""
    app = FastAPI(
        title="onchain_platform Research API",
        version="1.0",
        description="AI-native quant research platform — read-only research "
        "API over on-chain data (DOC-015).",
        openapi_url=f"{V1_PREFIX}/openapi.json",
    )

    # CORS: open for local dev, scoped to the Streamlit default origin
    # localhost:8501, GET-only (DOC-015 § Security & Cross-Origin Policy).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8501"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.middleware("http")(add_correlation_id_middleware)

    # Error handling (DOC-015 § Error Handling) — shared body + correlation_id.
    # FastAPI dispatches the most specific handler: PlatformError subclasses
    # land on platform_error_handler; routed HTTPExceptions (404/422) on
    # http_exception_handler; and Starlette's unmatched-route 404 (raised as
    # starlette.exceptions.HTTPException, base of fastapi.HTTPException) is
    # also wrapped — so EVERY error response shares the one body shape.
    # Cast to Any: FastAPI's ExceptionHandler typing is narrower (Pyright).
    app.add_exception_handler(PlatformError, platform_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]

    # Health (liveness-only, Q3).
    from onchain_platform.research.api.routes.health import router as health_router

    app.include_router(health_router, prefix=V1_PREFIX)

    # Resource routers (DOC-015 Endpoint Catalog) — mounted once, only here.
    # Sub-resource routers (bars/facts) are mounted BEFORE pairs so their
    # more specific `/pairs/{id}/bars` and `/pairs/{id}/facts` paths win over
    # pairs' greedy `{pair_id:path}` capture. Order matters — disable isort
    # so it does not alphabetically reorder and break the precedence.
    # isort: off
    from onchain_platform.research.api.routes.facts import router as facts_router
    from onchain_platform.research.api.routes.bars import router as bars_router
    from onchain_platform.research.api.routes.snapshots import router as snapshots_router
    from onchain_platform.research.api.routes.insights import router as insights_router
    from onchain_platform.research.api.routes.outcomes import router as outcomes_router
    from onchain_platform.research.api.routes.features import router as features_router
    from onchain_platform.research.api.routes.pairs import router as pairs_router
    from onchain_platform.research.api.routes.tokens import router as tokens_router
    from onchain_platform.research.api.routes.wallets import router as wallets_router
    # isort: on

    app.include_router(facts_router, prefix=V1_PREFIX)
    app.include_router(bars_router, prefix=V1_PREFIX)
    app.include_router(snapshots_router, prefix=V1_PREFIX)
    app.include_router(insights_router, prefix=V1_PREFIX)
    app.include_router(outcomes_router, prefix=V1_PREFIX)
    app.include_router(features_router, prefix=V1_PREFIX)
    app.include_router(pairs_router, prefix=V1_PREFIX)
    app.include_router(tokens_router, prefix=V1_PREFIX)
    app.include_router(wallets_router, prefix=V1_PREFIX)
    # Remaining resource routers (features/dataset) are added in later
    # phases, each mounted here once.

    # Serve the generated OpenAPI at the versioned path (DOC-015 § OpenAPI).
    @app.get(f"{V1_PREFIX}/openapi.json", include_in_schema=False, name="openapi")
    async def _serve_openapi() -> JSONResponse:
        return JSONResponse(app.openapi())

    return app
