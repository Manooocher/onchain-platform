"""Measure Research API response times (Reality Check / ops utility).

Read-only benchmark against a running API. Prints avg / p95 per endpoint.

Run (with the API up on :8000):
    uv run python scripts/benchmark_api.py
"""

import statistics
import time

import httpx

_BASE = "http://localhost:8000"
_ITERATIONS = 10

_ENDPOINTS = [
    "/v1/health",
    "/v1/pairs?limit=10",
    "/v1/pairs?limit=100",
    "/v1/pairs?limit=10&chain_id=8453",
    "/v1/strategy/rankings?limit=10",
]


def benchmark() -> None:
    if not _server_up():
        print(f"API not reachable at {_BASE} — is the API running?")
        return

    with httpx.Client(base_url=_BASE, timeout=30.0) as c:
        print("=== API Performance ===")
        for endpoint in _ENDPOINTS:
            times: list[float] = []
            for _ in range(_ITERATIONS):
                start = time.perf_counter()
                c.get(endpoint)
                times.append((time.perf_counter() - start) * 1000)
            avg = statistics.mean(times)
            p95 = sorted(times)[int(0.95 * len(times)) - 1]
            print(f"{endpoint:45} avg={avg:7.1f}ms  p95={p95:7.1f}ms")


def _server_up() -> bool:
    try:
        import httpx

        httpx.get(f"{_BASE}/v1/health", timeout=5.0)
        return True
    except Exception:  # noqa: BLE001 — probe failure just means "not up"
        return False


if __name__ == "__main__":
    benchmark()
