"""Multi-provider configuration loader (ADR-006 § Provider Abstraction).

Reads `config/providers.yaml` and resolves `{api_key}` / `{subdomain}`
placeholders from the environment. Provides dataclass-typed specs consumed by
`acquisition/providers/factory.py`.

API keys are NEVER embedded in the YAML — the file holds templates only, and
the loader reads the real values from environment variables (DOC-010 §
Security: secrets come from the environment, never version-controlled files).
"""

from dataclasses import dataclass
from os import environ
from pathlib import Path

import yaml

from onchain_platform.domain.exceptions import AcquisitionError

# Config path is repo-root config/ next to platform/ (like confirmation_depth).
_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "providers.yaml"


@dataclass(frozen=True)
class ProviderSpec:
    """A single provider definition, ready to build.

    `url` and `ws_url` are the RESOLVED endpoint URLs (placeholders expanded
    from the environment) — never the raw template.
    """

    name: str
    type: str
    url: str
    ws_url: str | None
    rate_limit_per_second: int
    priority: int
    requires_ssl_no_revoke: bool = False


@dataclass(frozen=True)
class ChainProviderConfig:
    """All providers for one chain in priority order + failover settings."""

    chain_id: int
    providers: list[ProviderSpec]  # ordered by priority (1 = first/primary)
    strategy: str
    health_check_interval_seconds: int
    unhealthy_threshold: int
    recovery_interval_seconds: int


def _resolve(template: str, template_fields: dict[str, str]) -> str:
    """Replace {env-name} placeholders with resolved values from template_fields."""
    result = template
    for placeholder, resolved in template_fields.items():
        result = result.replace("{" + placeholder + "}", resolved)
    return result


def _spec_from_yaml(entry: dict[str, object]) -> ProviderSpec:
    """Build a ProviderSpec from one provider's YAML block.

    Resolves `{api_key}` from `api_key_env` and `{subdomain}` from
    `subdomain_env` (if present). Missing required env vars raise.
    """
    name = _as_str(entry, "name")
    ptype = _as_str(entry, "type")
    url_template = _as_str(entry, "url_template")
    rate_limit = int(_as_str(entry, "rate_limit_per_second") or 0)
    priority = int(_as_str(entry, "priority") or 99)
    ssl_no_revoke = bool(_as_bool(entry, "requires_ssl_no_revoke"))

    fields: dict[str, str] = {}

    api_key_env = _as_str(entry, "api_key_env")
    if api_key_env:
        fields["api_key"] = _required_env(name, api_key_env)

    subdomain_env = _as_str(entry, "subdomain_env")
    if subdomain_env:
        fields["subdomain"] = _required_env(name, subdomain_env)

    url = _resolve(url_template, fields)

    ws_url: str | None = None
    ws_template = _as_str(entry, "ws_template")
    if ws_template:
        ws_url = _resolve(ws_template, fields)

    return ProviderSpec(
        name=name,
        type=ptype,
        url=url,
        ws_url=ws_url,
        rate_limit_per_second=rate_limit,
        priority=priority,
        requires_ssl_no_revoke=ssl_no_revoke,
    )


def _as_str(entry: dict[str, object], key: str) -> str:
    """Coerce a YAML value to str, tolerating None/absent."""
    value = entry.get(key)
    if value is None:
        return ""
    return str(value)


def _as_bool(entry: dict[str, object], key: str) -> bool:
    value = entry.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).lower() == "true"


def _required_env(provider_name: str, env: str) -> str:
    """Read a required env var, raising if it is unset or blank."""
    value = environ.get(env, "")
    if not value:
        raise AcquisitionError(
            f"provider {provider_name!r} requires env var {env} (see .env.example)"
        )
    return value


def load_provider_config(chain: str, path: Path | None = None) -> ChainProviderConfig:
    """Load and resolve the provider configuration for a chain.

    Reads config/providers.yaml (or `path`), selects the `chain` block, and
    returns a ChainProviderConfig with providers ordered by priority.
    """
    config_path = path or _DEFAULT_PATH
    try:
        raw = yaml.safe_load(config_path.read_text())
    except FileNotFoundError as exc:  # pragma: no cover — dev misconfig
        raise AcquisitionError(f"providers config not found at {config_path}") from exc

    chain_block = (raw or {}).get(chain)
    if chain_block is None:
        raise AcquisitionError(f"chain {chain!r} not present in providers config")

    chain_id = int(chain_block["chain_id"])

    # Collect all provider blocks whatever the YAML key names them.
    providers: list[ProviderSpec] = []
    for entry in chain_block.values():
        if not isinstance(entry, dict) or "type" not in entry:
            continue  # skip the `failover:` block and non-provider entries
        providers.append(_spec_from_yaml(entry))

    providers.sort(key=lambda p: p.priority)

    failover = dict(chain_block.get("failover", {}))
    return ChainProviderConfig(
        chain_id=chain_id,
        providers=providers,
        strategy=str(failover.get("strategy", "priority_with_health_check")),
        health_check_interval_seconds=int(failover.get("health_check_interval_seconds", 30)),
        unhealthy_threshold=int(failover.get("unhealthy_threshold", 3)),
        recovery_interval_seconds=int(failover.get("recovery_interval_seconds", 300)),
    )
