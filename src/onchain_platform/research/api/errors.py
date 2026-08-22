"""Error handling for the Research API (DOC-015 § Error Handling).

Every error response, regardless of status, shares one body shape:
    { "error": { "code": "...", "message": "...", "correlation_id": "..." } }

- 404 for a missing resource (RESOURCE_NOT_FOUND)
- 422 for malformed params (FastAPI's Pydantic validation default)
- 500 for a PlatformError/PersistenceError leaking to the boundary
- 500 (INTERNAL_ERROR) for anything else, treated as a bug

`code` is a stable machine-matchable string; `correlation_id` is generated
per-request and ties the client-visible error to server logs (DOC-013 §
Observability in Code) without exposing internals. A middleware attaches the
correlation_id to request.state (for log binding) and wraps unhandled
exceptions in the shared error body.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from onchain_platform.domain.exceptions import PlatformError


def _encode_correlation(request: Request) -> str:
    """Return the request's correlation_id, generating + caching it once."""
    cid = getattr(request.state, "correlation_id", None)
    if cid is None:
        cid = uuid.uuid4().hex
        request.state.correlation_id = cid
    return cid


def _error_body(request: Request, code: str, message: str) -> dict[str, object]:
    return {
        "error": {"code": code, "message": message, "correlation_id": _encode_correlation(request)}
    }


async def add_correlation_id_middleware(
    request: Request, call_next: Callable[..., Awaitable[Any]]
) -> Any:
    """ASGI middleware (FastAPI @app.middleware) that stamps correlation_id
    and captures unhandled exceptions into the shared error body."""
    correlation_id = _encode_correlation(request)
    try:
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation_id
        return response
    except Exception:  # unhandled → bug, per DOC-015
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "INTERNAL_ERROR", "An unexpected error occurred"),
        )


async def platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
    """Map any PlatformError (e.g. PersistenceError) reaching the boundary to
    a 500. The response never exposes the internal exception message — only a
    correlation ID (DOC-015 § Error Handling)."""
    return JSONResponse(
        status_code=500,
        content=_error_body(
            request,
            "PLATFORM_ERROR",
            "An internal platform error occurred",
        ),
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Map FastAPI HTTPException (404/422/...) into the shared error body."""
    status = exc.status_code
    if status == 404:
        code = "RESOURCE_NOT_FOUND"
    elif status == 422:
        code = "VALIDATION_ERROR"
    else:
        code = "HTTP_ERROR"
    return JSONResponse(
        status_code=status,
        content=_error_body(request, code, str(exc.detail)),
    )
