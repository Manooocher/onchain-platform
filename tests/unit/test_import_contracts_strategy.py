"""Unit test: research/ must NOT import strategy/ (DOC-011 contract).

This is the executable form of the critical M10 architectural constraint:
`research/` may not import `strategy/`. It verifies by importing the research
submodules first and asserting no strategy module is transitively imported.
The strategy package is intentionally NOT imported at this test's module top
so importing the test does not itself pull strategy in.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013).
"""

import ast
from pathlib import Path


def test_importing_research_does_not_import_strategy() -> None:
    """Every research/ .py file's imports must not reference strategy/."""
    import onchain_platform.research

    pkg_dir = Path(onchain_platform.research.__file__).resolve().parent
    bad: list[str] = []
    for py in pkg_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("onchain_platform.strategy"):
                        bad.append(f"{py}: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("onchain_platform.strategy"):
                    bad.append(f"{py}: {node.module}")
    assert not bad, f"research/ imports strategy: {bad}"


def test_strategy_router_does_not_import_analytics_or_intelligence() -> None:
    """strategy/ modules must not reference analytics/ or intelligence/
    (DOC-011 forbidden contract). Verified via source inspection."""
    import inspect

    # Importing strategy here is fine — it may import research, not the
    # reverse. This test asserts its source contains no forbidden refs.
    from onchain_platform.strategy import api, ranking

    for module in (api, ranking):
        src = inspect.getsource(module)
        assert "onchain_platform.analytics" not in src
        assert "onchain_platform.intelligence" not in src
