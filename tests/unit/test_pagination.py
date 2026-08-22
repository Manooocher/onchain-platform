"""Unit tests: cursor encoding/decoding (DOC-015 § Response Shape).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013).
"""

import pytest

from onchain_platform.research.api.pagination import (
    InvalidCursor,
    decode_cursor,
    encode_cursor,
)


def test_encode_decode_round_trip() -> None:
    keys = {"fact_id": "8453:0xabc:14"}
    encoded = encode_cursor(keys)
    assert decode_cursor(encoded) == keys


def test_encode_is_url_safe_no_padding_padding() -> None:
    # base64url output must not contain '/', '+' or '=' which break URLs.
    encoded = encode_cursor({"bar_start_time": "2026-06-01T00:00:00+00:00"})
    assert "/" not in encoded
    assert "+" not in encoded
    assert "=" not in encoded


def test_decode_malformed_raises_invalid_cursor() -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor("!!not-base64!!")
    with pytest.raises(InvalidCursor):
        decode_cursor("aGVsbG8=")  # valid b64 but not JSON


def test_decode_non_object_json_raises() -> None:
    import base64

    bad = base64.urlsafe_b64encode(b"[1,2,3]").decode()
    with pytest.raises(InvalidCursor):
        decode_cursor(bad)
