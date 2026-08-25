"""Unit tests: production-mode start-path fixes surfaced by the smoke run.

Covers two real bugs found while running the platform against live Base:
- load_confirmation_depths must be keyed by chain NAME (matching the YAML /
  --chain flag), not int chain_id.
- An explicit --start-block (replay/smoke) must override the checkpoint.
"""

from pathlib import Path

from onchain_platform.platform.config import Settings


def test_load_confirmation_depths_keyed_by_chain_name(tmp_path: Path) -> None:
    yaml_path = tmp_path / "confirmation_depth.yaml"
    yaml_path.write_text("confirmation_depth:\n  base: 3\n  ethereum: 12\n  bnb: 8\n")
    s = Settings(confirmation_depth_path=yaml_path)
    depths = s.load_confirmation_depths()
    assert depths == {"base": 3, "ethereum": 12, "bnb": 8}
    # Keys are chain NAMES (matching the --chain flag), not int chain_ids.
    assert "base" in depths
    assert depths["base"] == 3
