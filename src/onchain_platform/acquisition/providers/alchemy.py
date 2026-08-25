"""Alchemy provider (ADR-006 § Provider Abstraction).

Thin typed subclass of the shared HTTP JSON-RPC base. Alchemy exposes the
standard EVM RPC surface plus an optional WebSocket endpoint (`ws_url`), which
is preserved on the spec for future subscription support but unused by the
poll-based collector today.
"""

from onchain_platform.acquisition.providers.http_json_rpc import HttpJsonRpcProvider


class AlchemyProvider(HttpJsonRpcProvider):
    """Alchemy JSON-RPC over HTTP (Base mainnet)."""

    def __init__(
        self,
        rpc_url: str,
        *,
        ws_url: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            rpc_url,
            ws_url=ws_url,
            timeout_seconds=timeout_seconds,
        )
