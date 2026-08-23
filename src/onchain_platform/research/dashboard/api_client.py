"""Typed HTTPX client for the Research Platform API.

The dashboard NEVER imports persistence/ or domain/ directly. The API is the
only data path (DOC-015 § Dashboard: "never a second data path"). All reads
go through this client, which talks only to the FastAPI over HTTP.
"""

from typing import Any
from urllib.parse import quote

import httpx


class OnchainPlatformClient:
    """HTTPX client for the onchain_platform Research API (DOC-015)."""

    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30.0)

    # --- helpers ---

    def _url(self, path: str) -> str:
        return f"{self.base_url}/v1{path}"

    @staticmethod
    def _qid(entity_id: str) -> str:
        """Percent-encode a canonical ID for use as a path segment."""
        return quote(entity_id, safe="")

    # --- endpoints ---

    def get_health(self) -> dict[str, Any]:
        """GET /v1/health"""
        resp = self.client.get(self._url("/health"))
        resp.raise_for_status()
        return dict(resp.json())

    def get_pairs(
        self,
        chain_id: int | None = None,
        dex: str | None = None,
        created_after: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """GET /v1/pairs"""
        params: dict[str, Any] = {"limit": limit}
        if chain_id:
            params["chain_id"] = chain_id
        if dex:
            params["dex"] = dex
        if created_after:
            params["created_after"] = created_after
        if cursor:
            params["cursor"] = cursor
        resp = self.client.get(self._url("/pairs"), params=params)
        resp.raise_for_status()
        return dict(resp.json())

    def get_pair(self, pair_id: str) -> dict[str, Any]:
        """GET /v1/pairs/{pair_id}"""
        resp = self.client.get(self._url(f"/pairs/{self._qid(pair_id)}"))
        resp.raise_for_status()
        return dict(resp.json())

    def get_bars(
        self,
        pair_id: str,
        interval: str,
        start: str,
        end: str,
        include_provisional: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        """GET /v1/pairs/{pair_id}/bars"""
        params: dict[str, Any] = {
            "interval": interval,
            "start": start,
            "end": end,
            "include_provisional": include_provisional,
            "limit": limit,
        }
        resp = self.client.get(self._url(f"/pairs/{self._qid(pair_id)}/bars"), params=params)
        resp.raise_for_status()
        return dict(resp.json())

    def get_features(self, entity_id: str, as_of: str | None = None) -> dict[str, Any]:
        """GET /v1/entities/{entity_id}/features"""
        params: dict[str, Any] = {}
        if as_of:
            params["as_of"] = as_of
        resp = self.client.get(
            self._url(f"/entities/{self._qid(entity_id)}/features"), params=params
        )
        resp.raise_for_status()
        return dict(resp.json())

    def get_dataset(
        self,
        pair_id: str,
        interval: str,
        start: str,
        end: str,
        feature_names: str | None = None,
    ) -> dict[str, Any]:
        """GET /v1/pairs/{pair_id}/dataset"""
        params: dict[str, Any] = {"interval": interval, "start": start, "end": end}
        if feature_names:
            params["feature_names"] = feature_names
        resp = self.client.get(self._url(f"/pairs/{self._qid(pair_id)}/dataset"), params=params)
        resp.raise_for_status()
        return dict(resp.json())

    def get_rankings(
        self,
        chain_id: int | None = None,
        dex: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """GET /v1/strategy/rankings"""
        params: dict[str, Any] = {"limit": limit}
        if chain_id:
            params["chain_id"] = chain_id
        if dex:
            params["dex"] = dex
        resp = self.client.get(self._url("/strategy/rankings"), params=params)
        resp.raise_for_status()
        return dict(resp.json())

    def close(self) -> None:
        self.client.close()
