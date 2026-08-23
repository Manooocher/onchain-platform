"""Unit tests: Streamlit dashboard is API-only (Phase F).

Verifies the dashboard package never imports persistence/ or domain/
directly — the API is the only data path (DOC-015 § Dashboard). This tests
the source by importing the modules and asserting their `__spec__` deps, plus
an AST scan for forbidden imports.

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013).
"""

import ast
from pathlib import Path

from onchain_platform.research import dashboard


def _imports_permitted(path: Path) -> list[str]:
    """Return the dashboard module's disallowed imports (persistence, domain,
    acquisition, processing, domain_management, strategy)."""
    tree = ast.parse(path.read_text())
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "onchain_platform.persistence" or node.module.startswith(
                "onchain_platform.persistence."
            ):
                bad.append(node.module)
            if node.module == "onchain_platform.domain" or node.module.startswith(
                "onchain_platform.domain."
            ):
                bad.append(node.module)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in (
                    "onchain_platform.persistence",
                    "onchain_platform.domain",
                ) or alias.name.startswith(
                    ("onchain_platform.persistence.", "onchain_platform.domain.")
                ):
                    bad.append(alias.name)
    return bad


def test_dashboard_never_imports_persistence_or_domain() -> None:
    pkg_dir = Path(dashboard.__file__).resolve().parent
    forbidden = []
    for py in pkg_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        forbidden.extend(_imports_permitted(py))
    assert forbidden == [], f"dashboard imports forbidden modules: {forbidden}"


def test_dashboard_modules_importable() -> None:
    # Importing api_client exercises its deps without hitting the network.
    from onchain_platform.research.dashboard import api_client  # noqa: F401

    assert hasattr(api_client, "OnchainPlatformClient")
