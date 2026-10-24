"""RockX / W3Node provider (ADR-006 § Provider Abstraction).

Thin typed subclass of the shared HTTP JSON-RPC base. RockX serves the
standard EVM JSON-RPC surface; it inherits the base's TLS posture, which
verifies certificates (DOC-010 § Security). No provider is permitted to
disable TLS verification (SEC-2).
"""

from onchain_platform.acquisition.providers.http_json_rpc import HttpJsonRpcProvider


class RockXProvider(HttpJsonRpcProvider):
    """RockX / W3Node JSON-RPC over HTTP."""

    def __init__(
        self,
        rpc_url: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            rpc_url,
            ws_url=None,
            timeout_seconds=timeout_seconds,
        )
