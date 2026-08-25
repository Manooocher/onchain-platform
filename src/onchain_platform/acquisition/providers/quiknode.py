"""QuickNode provider (ADR-006 § Provider Abstraction).

Thin typed subclass of the shared HTTP JSON-RPC base. QuickNode uses standard
EVM JSON-RPC; QuickNode-specific error handling/rate-limit codes are handled
by the base's retry translation (fixed code set) plus the orchestrator's
token-bucket limiter.
"""

from onchain_platform.acquisition.providers.http_json_rpc import HttpJsonRpcProvider


class QuickNodeProvider(HttpJsonRpcProvider):
    """QuickNode JSON-RPC over HTTP."""

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
