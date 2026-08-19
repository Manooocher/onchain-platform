"""structlog configuration (DOC-010 § Observability; DOC-013 § Observability
in Code).

Logging is the deliberate exception to dependency injection (DOC-013 §
Dependency & Composition): structlog.get_logger(__name__) at module scope is
fine — the processor chain below is configured exactly once, at process
startup, by main.py.

Enforcement (DOC-013 § Observability in Code): a log entry originating from
a Capability module must carry that Capability's mandatory structured
fields. The capability_fields processor checks this and marks incomplete
entries with _missing_capability_fields instead of crashing the pipeline —
the marker is observable in every JSON log line and asserted in unit tests.
"""

from collections.abc import MutableMapping
from typing import Any

import structlog

# Mandatory structured fields per Capability (DOC-013 § Observability in
# Code). tx_hash is "when applicable" — chain_id + block_number are the
# always-required pair for acquisition/processing.
_MANDATORY_FIELDS: dict[str, tuple[str, ...]] = {
    "onchain_platform.acquisition": ("chain_id", "block_number"),
    "onchain_platform.processing": ("chain_id", "block_number"),
    "onchain_platform.domain_management": ("entity_id",),
    "onchain_platform.analytics": ("entity_id", "as_of_timestamp"),
    "onchain_platform.intelligence": ("entity_id",),
    "onchain_platform.research": ("request_id",),
    "onchain_platform.strategy": ("request_id",),
}

Processor = Any


def capability_fields_processor(
    logger: object, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mark log entries that lack their Capability's mandatory fields.

    A successfully-handled reorg or a routine PENDING fact still logs at
    INFO (DOC-013 § Log level policy) — this processor never changes
    levels; it only enforces field presence.
    """
    logger_name = str(event_dict.get("logger", ""))
    for prefix, required in _MANDATORY_FIELDS.items():
        if logger_name.startswith(prefix):
            missing = [field for field in required if field not in event_dict]
            if missing:
                event_dict["_missing_capability_fields"] = missing
            break
    return event_dict


def configure_logging() -> None:
    """Configure structlog exactly once, at process startup (DOC-013 §
    Dependency & Composition — logging is configured in main.py)."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            capability_fields_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
