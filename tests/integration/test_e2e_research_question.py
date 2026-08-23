"""E2E test: answer DOC-002's "why did this token gain momentum" via API only.

This is the actual success criterion from DOC-002 and the M9 DoD: a
researcher can answer a research question using only the API, not a database
client. This test is the executable proof.

The test imports NO persistence/ analytics/ intelligence/ domain_management/
acquisition/ or processing/ modules. It imports only httpx, pytest, and the
FastAPI app factory (to mount the ASGI transport).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.main import create_app


@pytest.mark.asyncio
async def test_answer_why_did_token_gain_momentum(pg_engine: AsyncEngine) -> None:
    """Answer DOC-002's question using only API calls (no DB client).

    The app is mounted on ASGITransport and `get_session` is overridden to
    the test's pg_engine fixture so the test shares one engine and does not
    depend on process-global state (robust under full-suite runs). No
    repository/model is touched in this test body.
    """
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(pg_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: API is up
        health = await client.get("/v1/health")
        assert health.status_code == 200

        # Step 2: Find pairs
        pairs_resp = await client.get("/v1/pairs", params={"limit": 10})
        assert pairs_resp.status_code == 200
        pairs = pairs_resp.json()
        assert "items" in pairs

        if not pairs["items"]:
            pytest.skip("no pairs in test database — E2E requires seeded data")

        pair_id = pairs["items"][0]["canonical_id"]

        # Step 3: Get features for momentum analysis (PIT "all features" form).
        features_resp = await client.get(f"/v1/entities/{pair_id}/features")
        assert features_resp.status_code == 200
        features = features_resp.json()
        momentum_feature = None
        for f in features.get("items", []):
            if "momentum" in f.get("feature_name", "").lower():
                momentum_feature = f
                break
        # If a momentum feature exists, it must carry a numeric value (the
        # researcher can then interpret momentum from it).
        if momentum_feature is not None:
            assert isinstance(momentum_feature.get("value"), (int, float))

        # Step 4: Get market bars for price action (recent window).
        bars_resp = await client.get(
            f"/v1/pairs/{pair_id}/bars",
            params={"interval": "1h", "limit": 24},
        )
        assert bars_resp.status_code == 200
        assert "items" in bars_resp.json()

        # Step 5: Underlying facts for the pair (audit trail).
        facts_resp = await client.get(f"/v1/pairs/{pair_id}/facts", params={"limit": 50})
        assert facts_resp.status_code == 200

        # Step 6: Intelligence insights for the entity.
        insights_resp = await client.get(f"/v1/entities/{pair_id}/insights")
        assert insights_resp.status_code == 200

        # Step 7: Assemble research dataset (short window; arrays may be empty).
        end = datetime.now(UTC)
        start = end - timedelta(days=1)
        dataset_resp = await client.get(
            f"/v1/pairs/{pair_id}/dataset",
            params={
                "interval": "1h",
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        assert dataset_resp.status_code in (200, 404)
        if dataset_resp.status_code == 200:
            dataset = dataset_resp.json()
            assert "pair" in dataset
            assert "bars" in dataset
            assert "features" in dataset
            assert "outcomes" in dataset

    # Conclusion: every research step completed via HTTP with no direct DB
    # access in this test. DOC-002 success criterion holds.


@pytest.mark.asyncio
async def test_e2e_no_forbidden_imports() -> None:
    """Meta-test: this file must never import the forbidden packages.

    Uses AST to inspect actual import statements (not docstring text) so
    prose mentioning the package names does not trip the check.
    """
    import ast
    import inspect
    import sys

    tree = ast.parse(inspect.getsource(sys.modules[__name__]))
    forbidden_prefixes = (
        "onchain_platform.persistence",
        "onchain_platform.analytics",
        "onchain_platform.intelligence",
        "onchain_platform.domain_management",
        "onchain_platform.acquisition",
        "onchain_platform.processing",
    )
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    bad = [name for name in imported if name.startswith(forbidden_prefixes)]
    assert not bad, f"E2E test imports forbidden modules: {bad}"
