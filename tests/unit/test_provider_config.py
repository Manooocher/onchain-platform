"""Unit tests: multi-provider configuration loader (Phase A).

Covers placeholder resolution from env, priority ordering, and rejection of
missing required env vars. Uses a temp providers YAML so tests do not depend
on the on-disk config or a real environment.
"""

from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from onchain_platform.domain.exceptions import AcquisitionError
from onchain_platform.platform.provider_config import load_provider_config


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "providers.yaml"
    p.write_text(body)
    return p


BASE_YAML = """
base:
  chain_id: 8453
  primary:
    name: alchemy
    type: alchemy
    url_template: "https://base-mainnet.g.alchemy.com/v2/{api_key}"
    api_key_env: "ALCHEMY_BASE_API_KEY"
    rate_limit_per_second: 660
    priority: 1
  secondary:
    name: quiknode
    type: quiknode
    url_template: "https://{subdomain}.base-mainnet.quiknode.pro/{api_key}/"
    subdomain_env: "QUICKNODE_BASE_SUBDOMAIN"
    api_key_env: "QUICKNODE_BASE_API_KEY"
    rate_limit_per_second: 50
    priority: 2
  tertiary:
    name: rockx
    type: rockx_w3node
    url_template: "https://base.w3node.com/{api_key}/api"
    api_key_env: "ROCKX_BASE_API_KEY"
    rate_limit_per_second: 25
    priority: 3
    requires_ssl_no_revoke: true
  failover:
    strategy: "priority_with_health_check"
    health_check_interval_seconds: 30
    unhealthy_threshold: 3
    recovery_interval_seconds: 300
"""


def _set_keys(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ALCHEMY_BASE_API_KEY", "alch_test")
    monkeypatch.setenv("QUICKNODE_BASE_API_KEY", "qn_test")
    monkeypatch.setenv("QUICKNODE_BASE_SUBDOMAIN", "thumb-bubble-1")
    monkeypatch.setenv("ROCKX_BASE_API_KEY", "rockx_test")


def test_load_provider_config_resolves_placeholders(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _set_keys(monkeypatch)
    path = _write_yaml(tmp_path, BASE_YAML)

    cfg = load_provider_config("base", path=path)

    assert cfg.chain_id == 8453
    # Ordered by priority.
    assert [p.name for p in cfg.providers] == ["alchemy", "quiknode", "rockx"]
    # URLs resolved from env.
    assert cfg.providers[0].url == "https://base-mainnet.g.alchemy.com/v2/alch_test"
    assert cfg.providers[1].url == "https://thumb-bubble-1.base-mainnet.quiknode.pro/qn_test/"
    # rate limit + typed.
    assert cfg.providers[0].rate_limit_per_second == 660
    assert cfg.providers[2].requires_ssl_no_revoke is True
    # failover settings.
    assert cfg.strategy == "priority_with_health_check"
    assert cfg.health_check_interval_seconds == 30
    assert cfg.recovery_interval_seconds == 300
    assert cfg.unhealthy_threshold == 3


def test_load_provider_config_missing_chain_raises(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _set_keys(monkeypatch)
    path = _write_yaml(tmp_path, BASE_YAML)
    with pytest.raises(AcquisitionError, match="not present"):
        load_provider_config("ethereum", path=path)


def test_load_provider_config_missing_env_key_raises(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # No env vars set — placeholder cannot be resolved.
    path = _write_yaml(tmp_path, BASE_YAML)
    with pytest.raises(AcquisitionError, match="requires env var"):
        load_provider_config("base", path=path)
