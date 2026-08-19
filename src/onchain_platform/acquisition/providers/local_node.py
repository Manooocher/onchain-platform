"""LocalNode / plain-endpoint JSON-RPC provider (ADR-006 Option A: raw
JSON-RPC over HTTP, behind the BlockchainProvider interface).

Works against any standard EVM JSON-RPC endpoint — the Base public endpoint
by default, or any provider URL supplied via configuration. The provider is
replaceable infrastructure (ADR-006 Principle 6): swapping in
acquisition/providers/alchemy.py later requires no change to the Collector,
the Fact Processor, or anything downstream.

Design notes:
- Explicit timeout on every call (DOC-013 § Async Conventions — a provider
  that hangs instead of erroring is the failure mode failover exists to
  route around, and failover cannot trigger on a call that never returns).
- Bounded retry with a fixed backoff schedule for transient transport
  errors only; JSON-RPC-level errors (rate limits, invalid params) are
  translated to AcquisitionError immediately — retrying them would mask a
  real misconfiguration.
- Every httpx exception is translated to AcquisitionError before leaving
  this package (DOC-013 § Exception Hierarchy: never a raw httpx error
  past the acquisition/ boundary).
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

# Transient JSON-RPC error codes worth one bounded retry (provider
# overload / temporary unavailability). Rate-limit codes retry too — a free
# tier burst is expected behavior, not a failure (Risk Register § rate
# limits).
_RETRYABLE_RPC_CODES = {-32000, -32005, -32029, -32603}
# Fixed backoff schedule: deterministic, no wall-clock reads (DOC-013 §
# Determinism Discipline — asyncio.sleep durations are config, not time).
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)


class LocalNodeProvider(BlockchainProvider):
    """JSON-RPC over HTTPX against a plain endpoint URL."""

    def __init__(self, rpc_url: str, timeout_seconds: float = 30.0) -> None:
        self._rpc_url = rpc_url
        # Explicit timeout on the client: no call may rely on a library
        # default (DOC-013 § Async Conventions).
        self._client = httpx.AsyncClient(timeout=timeout_seconds)
        self._request_id = 0

    async def get_chain_id(self) -> int:
        result = await self._call("eth_chainId", [])
        return _hex_to_int(result)

    async def get_chain_head(self) -> int:
        result = await self._call("eth_blockNumber", [])
        return _hex_to_int(result)

    async def get_block_metadata(self, block_number: int) -> BlockMetadata:
        result = await self._call("eth_getBlockByNumber", [hex(block_number), False])
        block = cast("dict[str, Any]", result)
        if block is None:
            raise AcquisitionError(f"block {block_number} not found via RPC")
        return BlockMetadata(
            number=_hex_to_int(block["number"]),
            hash=_lower(block["hash"]),
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
        # Canonical order, independent of provider return order (DOC-013 §
        # Determinism Discipline): block_number ascending, then log_index.
        normalized.sort(key=lambda log: (log.block_number, log.log_index))
        return normalized

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # internals
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
                # Translate at the boundary — a raw httpx exception must
                # never leave acquisition/ (DOC-013 § Exception Hierarchy).
                last_error = exc
                if attempt < len(_BACKOFF_SECONDS):
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise AcquisitionError(f"RPC transport failure calling {method}: {exc}") from exc

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
        # Unreachable: the loop always returns or raises. Kept for mypy.
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
    """One provider log entry → canonical RawLog (lowercase hashes, int
    indices). Provider-specific extra fields are ignored by construction —
    RawLog models only the canonical set (base.py docstring)."""
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
