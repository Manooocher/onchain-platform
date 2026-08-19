"""Fact Processor — canonical shape → Blockchain Fact (Pending) (DOC-011 §
processing/, ImplementationPlan § Milestone 1).

Milestone 1 scope: PAIR_CREATED only, PENDING only. The full Confirmation
Lifecycle (CONFIRMED/FINALIZED/ORPHANED) is deliberately NOT here — that is
Milestone 2 (processing/finality_engine.py), separated so this milestone
stays small enough to actually finish (ImplementationPlan § Milestone 1).

Determinism (DOC-013 § Determinism Discipline): no wall-clock reads —
ingested_at comes from the injected clock, observed_at from the collected
log (stamped at receipt by the collector). A replay with pinned clock
values therefore produces byte-identical output (ADR-006 Principle 2).
"""

from collections.abc import Callable
from datetime import datetime

import structlog

from onchain_platform.acquisition.collector import CollectedLog
from onchain_platform.domain.exceptions import DomainValidationError
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    PairCreatedPayload,
)
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType
from onchain_platform.processing.normalizer import (
    PAIR_CREATED_TOPIC,
    normalize_pair_created,
)

logger = structlog.get_logger(__name__)

SCHEMA_VERSION = "1.0"


class FactProcessor:
    """Turns collected logs into PENDING BlockchainFacts.

    Dependencies are injected (DOC-013 § Dependency & Composition): chain_id
    is configuration, clock produces ingested_at. No session is stored here —
    persistence is the composition root's wiring concern, and sessions stay
    scoped to the call that uses them (DOC-013 § Async Conventions).
    """

    def __init__(self, *, chain_id: int, clock: Callable[[], datetime]) -> None:
        self._chain_id = chain_id
        self._clock = clock

    def process(self, collected: CollectedLog) -> BlockchainFact:
        """Normalize one collected log into a PENDING BlockchainFact.

        Raises DomainValidationError for logs that are not PAIR_CREATED or
        cannot be decoded — callers (the replay handler, main.py) decide
        whether that is fatal.
        """
        if collected.raw_log.topics[0] != PAIR_CREATED_TOPIC:
            # The collector filters on topic0, so this is defense in depth —
            # a misconfigured topic must fail loudly, never silently produce
            # garbage facts (ADR-006 Principle 1: the platform owns the
            # canonical transformation).
            raise DomainValidationError(
                f"FactProcessor received a non-PairCreated log "
                f"(topic0={collected.raw_log.topics[0]!r})"
            )

        normalized = normalize_pair_created(collected)

        payload = PairCreatedPayload(
            fact_type="PAIR_CREATED",
            pair_address=normalized.pair_address,
            token0_address=normalized.token0_address,
            token1_address=normalized.token1_address,
            dex=normalized.dex,
        )
        # DOC-012 § Modeling the discriminated payload: fact_type and
        # payload.fact_type are intentionally the same value in two places;
        # enforcing their sync is THIS file's job, not the schema's.
        fact_type = FactType(payload.fact_type)
        if fact_type != FactType.PAIR_CREATED:  # structurally impossible; guard anyway
            raise DomainValidationError("fact_type/payload.fact_type sync violated")

        fact = BlockchainFact(
            schema_version=SCHEMA_VERSION,
            # Natural key, ':'-safe for fact_id (DOC-012 § Composite ID
            # Delimiter): chain_id and log_index are bare ints, tx_hash is
            # 0x-hex without colons.
            fact_id=f"{self._chain_id}:{normalized.tx_hash}:{normalized.log_index}",
            chain_id=self._chain_id,
            fact_type=fact_type,
            block_number=normalized.block_number,
            block_hash=normalized.block_hash,
            tx_hash=normalized.tx_hash,
            log_index=normalized.log_index,
            event_time=normalized.event_time,
            observed_at=collected.observed_at,
            ingested_at=self._clock(),
            # Milestone 1 persists PENDING only; the Finality Engine owns
            # every later transition (Milestone 2).
            confirmation_status=ConfirmationStatus.PENDING,
            confirmations=0,
            payload=payload,
        )
        # Mandatory structured fields for processing/ (DOC-013 §
        # Observability in Code).
        logger.info(
            "fact_created",
            chain_id=self._chain_id,
            block_number=fact.block_number,
            tx_hash=fact.tx_hash,
            fact_id=fact.fact_id,
            confirmation_status=fact.confirmation_status.value,
        )
        return fact
