"""Day-0 smoke test: the quality gates must collect at least one test.

An empty test directory makes pytest exit with code 5 ("no tests ran"),
which would fail CI silently-looking green gates cannot exist
(ImplementationPlan § Continuous Practices — gates run from Day 0).
This test also pins the minimum runtime contract the whole platform
depends on (DOC-010 § Runtime, § Data Processing).
"""

import pydantic


def test_toolchain_pydantic_v2_available() -> None:
    assert pydantic.VERSION.startswith("2.")
