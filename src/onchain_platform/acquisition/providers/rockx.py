"""RockX / W3Node provider (ADR-006 § Provider Abstraction).

RockX serves the standard EVM JSON-RPC surface but its TLS posture has
build/trust quirks in some environments (DOC-010: shortest path to
validation). Per the provider config (`requires_ssl_no_revoke`), this client
disables cert verification as an explicit, documented trade-off for reaching
the endpoint — the orchestrator health checker will route around it if it
fails. `verify=False` is scoped to this provider class; it is NOT global.
"""

import warnings

from onchain_platform.acquisition.providers.http_json_rpc import HttpJsonRpcProvider


class RockXProvider(HttpJsonRpcProvider):
    """RockX / W3Node JSON-RPC over HTTP (SSL verification disabled)."""

    _verify_tls: bool = False

    def __init__(
        self,
        rpc_url: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not self._verify_tls:
            warnings.warn(
                "RockXProvider disables TLS cert verification (requires_ssl_no_revoke). "
                "Restrict this provider to trusted endpoints only.",
                stacklevel=2,
            )
        super().__init__(
            rpc_url,
            ws_url=None,
            timeout_seconds=timeout_seconds,
        )
