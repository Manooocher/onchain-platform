"""Cursor encoding/decoding for the Research API (DOC-015 § Response Shape).

Cursor-based pagination is mandatory (never offset): a cursor is an opaque,
base64-URL-encoded JSON object built from the last row's own ordering key
(e.g. `{"fact_id": "..."}`, `{"bar_start_time": "..."}`). It is stable
regardless of rows appended mid-pagination.
"""

import base64
import json
from typing import Any


class InvalidCursor(Exception):
    """Raised when a cursor cannot be decoded (client bug → 422)."""


def encode_cursor(keys: dict[str, Any]) -> str:
    """Encode ordering keys as a base64-URL-safe JSON string (no padding).

    base64url output can carry `=` padding; it is stripped so the cursor is
    URL-safe in any context and padding-free (DOC-015 § Response Shape).
    """
    json_str = json.dumps(keys, separators=(",", ":"))
    return base64.urlsafe_b64encode(json_str.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode an opaque cursor back into ordering keys.

    Raises InvalidCursor on malformed input — the router maps this to a 422
    (DOC-015 § Error Handling), not a 500.
    """
    try:
        # Re-apply padding stripped on encode (urlsafe_b64decode rejects
        # unterminated base64 without it).
        padded = cursor + "=" * (-len(cursor) % 4)
        json_str = base64.urlsafe_b64decode(padded.encode()).decode()
        keys = json.loads(json_str)
        if not isinstance(keys, dict):
            raise InvalidCursor("cursor payload must be a JSON object")
        return keys
    except InvalidCursor:
        raise
    except Exception as exc:  # b64/JSON/type errors are all client bugs
        raise InvalidCursor(f"malformed cursor: {exc}") from exc
