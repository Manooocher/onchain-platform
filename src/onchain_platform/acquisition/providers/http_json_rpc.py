"""Shared HTTP JSON-RPC base for provider implementations (ADR-006 §
Provider Abstraction).

All three hosted providers (Alchemy, QuickNode, RockX/W3Node) speak the same
standard EVM JSON-RPC over HTTP. This base factors the common transport /
retry / normalization logic so each concrete provider is a thin, explicitly
typed subclass that only differs in transport details (e.g. SSL handling).

Contracts honored:
- Explicit timeout on every call (DOC-013 § Async Conventions).
- Bounded retry with a fixed backoff schedule for transient errors only;
  JSON-RPC-level errors translate to AcquisitionError immediately.
- Every httpx exception becomes AcquisitionError before leaving acquisition/
  (DOC-013 § Exception Hierarchy).
- Logs normalized to canonical RawLog form; deterministic ordering
  (block_number asc, log_index asc — DOC-013 § Determinism Discipline).
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from onchain_platform.acquisition.providers.base import (
    BlockchainProvider,
    BlockMetadata,
    RawLog,
)
from onchain_platform.domain.exceptions import AcquisitionError

# Transient JSON-RPC codes worth a bounded retry.
_RETRYABLE_RPC_CODES = {-32000, -32005, -32029, -32603}
# Fixed backoff: 0.5s, 1.0s, 2.0s.
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)


class HttpJsonRpcProvider(BlockchainProvider):
    """JSON-RPC over HTTP against a single endpoint URL."""

    # The httpx verify flag; subclasses that need a custom SSL posture (e.g.
    # RockX/W3Node) override this. Reasoned at the class, never the call site.
    _verify_tls: bool = True

    def __init__(
        self,
        rpc_url: str,
        *,
        ws_url: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._rpc_url = rpc_url
        self._ws_url = ws_url
        # Explicit timeout; no call may rely on a library default (DOC-013).
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            verify=self._verify_tls,
        )
        self._request_id = 0

    # ------------------------------------------------------------------
    # BlockchainProvider interface
    # ------------------------------------------------------------------

    async def get_chain_id(self) -> int:
        return _hex_to_int(await self._call("eth_chainId", []))

    async def get_chain_head(self) -> int:
        return _hex_to_int(await self._call("eth_blockNumber", []))

    async def get_block_metadata(self, block_number: int) -> BlockMetadata:
        result = await self._call("eth_getBlockByNumber", [hex(block_number), False])
        block = cast("dict[str, Any]", result)
        if block is None:
            raise AcquisitionError(f"block {block_number} not found via RPC")
        return BlockMetadata(
            number=_hex_to_int(block["number"]),
            hash=_lower(block["hash"]),
            parent_hash=_lower(block["parentHash"]),
            timestamp=datetime.fromtimestamp(_hex_to_int(block["timestamp"]), tz=UTC),
        )

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> list[RawLog]:
        params: dict[str, Any] = {"fromBlock": hex(from_block), "toBlock": hex(to_block)}
        if address is not None:
            params["address"] = address
        if topics is not None:
            params["topics"] = [list(topics)]
        result = await self._call("eth_getLogs", [params])
        logs = cast("list[dict[str, Any]]", result)
        normalized = [_normalize_log(entry) for entry in logs]
        normalized.sort(key=lambda log: (log.block_number, log.log_index))
        return normalized

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # transport internals
    # ------------------------------------------------------------------

    async def _call(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        envelope = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        last_error: Exception | None = None
        for attempt in range(len(_BACKOFF_SECONDS) + 1):
            try:
                response = await self._client.post(self._rpc_url, json=envelope)
                response.raise_for_status()
                body = cast("dict[str, Any]", response.json())
            except httpx.HTTPError as exc:
                # Translate at the boundary (DOC-013 § Exception Hierarchy).
                last_error = exc
                if attempt < len(_BACKOFF_SECONDS):
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise AcquisitionError(
                    f"RPC transport failure calling {method}: {exc}"
                ) from exc

            if "error" in body:
                error = cast("dict[str, Any]", body["error"])
                code = int(error.get("code", 0))
                message = str(error.get("message", ""))
                if code in _RETRYABLE_RPC_CODES and attempt < len(_BACKOFF_SECONDS):
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise AcquisitionError(
                    f"RPC error calling {method}: code={code} message={message!r}"
                )
            return body.get("result")
        raise AcquisitionError(f"RPC call {method} failed: {last_error}")


def _hex_to_int(value: Any) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise AcquisitionError(f"expected 0x-hex quantity from RPC, got {value!r}")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise AcquisitionError(f"malformed 0x-hex quantity from RPC: {value!r}") from exc


def _lower(value: Any) -> str:
    if not isinstance(value, str):
        raise AcquisitionError(f"expected hex string from RPC, got {value!r}")
    return value.lower()


def _normalize_log(entry: dict[str, Any]) -> RawLog:
    try:
        return RawLog(
            address=_lower(entry["address"]),
            topics=tuple(_lower(t) for t in entry["topics"]),
            data=_lower(entry["data"]),
            block_number=_hex_to_int(entry["blockNumber"]),
            block_hash=_lower(entry["blockHash"]),
            transaction_hash=_lower(entry["transactionHash"]),
            transaction_index=_hex_to_int(entry["transactionIndex"]),
            log_index=_hex_to_int(entry["logIndex"]),
            removed=bool(entry.get("removed", False)),
        )
    except KeyError as exc:
        raise AcquisitionError(f"malformed log entry from RPC: missing {exc}") from exc
