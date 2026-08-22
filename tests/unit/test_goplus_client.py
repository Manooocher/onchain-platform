"""Unit tests: GoPlus Client (Milestone 7).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions). Uses mocked httpx responses.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onchain_platform.domain.exceptions import AcquisitionError
from onchain_platform.intelligence.goplus_client import GoPlusClient


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.exists = AsyncMock(return_value=False)
    redis.incr = AsyncMock()
    redis.expire = AsyncMock()
    redis.eval = AsyncMock(return_value=1)  # token bucket always has tokens
    return redis


@pytest.mark.asyncio
async def test_successful_response(mock_redis: AsyncMock) -> None:
    client = GoPlusClient(mock_redis)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "code": 1,
        "message": "ok",
        "result": {"0xabc": {"is_honeypot": "0", "sell_tax": "0.05"}},
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_http

        result = await client.get_token_security(8453, "0xabc")

    assert result is not None
    assert result["is_honeypot"] == "0"
    assert result["sell_tax"] == "0.05"
    await client.close()


@pytest.mark.asyncio
async def test_cache_hit(mock_redis: AsyncMock) -> None:
    mock_redis.get = AsyncMock(return_value=b'{"is_honeypot": "0", "sell_tax": "0.05"}')
    client = GoPlusClient(mock_redis)

    result = await client.get_token_security(8453, "0xabc")
    assert result is not None
    assert result["is_honeypot"] == "0"
    # No HTTP request made (cache hit).
    await client.close()


@pytest.mark.asyncio
async def test_no_data_returns_none(mock_redis: AsyncMock) -> None:
    client = GoPlusClient(mock_redis)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "code": 1,
        "message": "ok",
        "result": {},  # no data for this address
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_http

        result = await client.get_token_security(8453, "0xabc")

    assert result is None
    await client.close()


@pytest.mark.asyncio
async def test_api_error_returns_none(mock_redis: AsyncMock) -> None:
    client = GoPlusClient(mock_redis)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "code": 0,
        "message": "error",
        "result": {},
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_http

        result = await client.get_token_security(8453, "0xabc")

    assert result is None
    await client.close()


@pytest.mark.asyncio
async def test_timeout_raises_acquisition_error(mock_redis: AsyncMock) -> None:
    import httpx

    client = GoPlusClient(mock_redis, timeout=0.01)

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_get.return_value = mock_http

        with pytest.raises(AcquisitionError, match="failed after"):
            await client.get_token_security(8453, "0xabc")

    await client.close()


@pytest.mark.asyncio
async def test_daily_quota_exceeded_returns_none(mock_redis: AsyncMock) -> None:
    # Cache miss for goplus:*, but daily CU at quota.
    async def mock_get(key: str) -> bytes | None:
        if key.startswith("goplus_daily_cu"):
            return b"28000"
        return None

    mock_redis.get = AsyncMock(side_effect=mock_get)
    client = GoPlusClient(mock_redis)

    result = await client.get_token_security(8453, "0xabc")
    assert result is None
    await client.close()
