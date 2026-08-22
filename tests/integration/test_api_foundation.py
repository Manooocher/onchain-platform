"""Integration tests: Research API foundation (Phase A).

Verifies the app factory: health liveness, /v1/openapi.json serving, CORS
scoped to localhost:8501 with GET-only, and the shared error body with
correlation_id (DOC-015 § OpenAPI, § Security, § Error Handling).
"""

from fastapi.testclient import TestClient

from onchain_platform.research.api.main import create_app


def test_health_liveness_ok() -> None:
    client = TestClient(create_app())
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_openapi_served_at_versioned_path() -> None:
    client = TestClient(create_app())
    resp = client.get("/v1/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "paths" in data
    assert "/v1/health" in data["paths"]
    # Every path operation is GET (read-only, DOC-015).
    for path, ops in data["paths"].items():
        for method in ops:
            assert method == "get", f"{path} has non-GET method {method}"


def test_cors_allows_localhost_8501_get() -> None:
    client = TestClient(create_app())
    resp = client.options(
        "/v1/health",
        headers={
            "Origin": "http://localhost:8501",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:8501"


def test_missing_resource_returns_shared_error_body() -> None:
    client = TestClient(create_app())
    resp = client.get("/v1/pairs/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "correlation_id" in body["error"]


def test_correlation_id_header_present() -> None:
    client = TestClient(create_app())
    resp = client.get("/v1/health")
    assert resp.headers.get("x-correlation-id") is not None
