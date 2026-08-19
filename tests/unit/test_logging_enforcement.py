"""Unit tests: structured-logging enforcement (DOC-013 § Observability in
Code)."""

from typing import Any

from onchain_platform.platform.logging import capability_fields_processor


def _entry(logger: str, **fields: Any) -> dict[str, Any]:
    event_dict: dict[str, Any] = {"logger": logger, "event": "test"}
    event_dict.update(fields)
    return event_dict


def test_capability_fields_processor_marks_missing_fields() -> None:
    # acquisition/ and processing/ must carry chain_id + block_number
    # (DOC-013 § Observability in Code).
    entry = _entry("onchain_platform.acquisition.collector")
    result = capability_fields_processor(None, "info", entry)
    assert result["_missing_capability_fields"] == ["chain_id", "block_number"]


def test_capability_fields_processor_accepts_complete_entry() -> None:
    entry = _entry("onchain_platform.processing.fact_processor", chain_id=8453, block_number=1)
    result = capability_fields_processor(None, "info", entry)
    assert "_missing_capability_fields" not in result


def test_capability_fields_processor_ignores_non_capability_loggers() -> None:
    # platform/ and main.py have no mandatory fields (DOC-013 table lists
    # only the seven Capabilities).
    entry = _entry("onchain_platform.platform.config")
    result = capability_fields_processor(None, "info", entry)
    assert "_missing_capability_fields" not in result


def test_capability_fields_processor_tx_hash_not_required() -> None:
    # tx_hash is "when applicable" — chain_id + block_number suffice
    # (DOC-013 § Observability in Code).
    entry = _entry("onchain_platform.acquisition.collector", chain_id=8453, block_number=5)
    result = capability_fields_processor(None, "info", entry)
    assert "_missing_capability_fields" not in result
