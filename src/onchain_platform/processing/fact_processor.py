"""Fact Processor — canonical shape → Blockchain Fact (Pending) (DOC-011 §
processing/).

Handles both PAIR_CREATED and SWAP_EXECUTED fact types. The full
Confirmation Lifecycle (CONFIRMED/FINALIZED/ORPHANED) is owned by
processing/finality_engine.py (Milestone 2).

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
    LiquidityAddedPayload,
    LiquidityRemovedPayload,
    PairCreatedPayload,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType
from onchain_platform.processing.normalizer import (
    BURN_TOPIC,
    MINT_TOPIC,
    PAIR_CREATED_TOPIC,
    SWAP_TOPIC,
    normalize_liquidity,
    normalize_pair_created,
    normalize_swap,
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

        Dispatches on topics[0] to the appropriate normalizer. Raises
        DomainValidationError for unknown topics — callers decide whether
        that is fatal.
        """
        topic0 = collected.raw_log.topics[0]

        if topic0 == PAIR_CREATED_TOPIC:
            return self._process_pair_created(collected)
        elif topic0 == SWAP_TOPIC:
            return self._process_swap(collected)
        elif topic0 in (MINT_TOPIC, BURN_TOPIC):
            return self._process_liquidity(collected)
        else:
            raise DomainValidationError(
                f"FactProcessor received log with unknown topic0 {topic0!r} "
                f"(tx={collected.raw_log.transaction_hash}, "
                f"logIndex={collected.raw_log.log_index})"
            )

    def _process_pair_created(self, collected: CollectedLog) -> BlockchainFact:
        normalized = normalize_pair_created(collected)

        payload = PairCreatedPayload(
            fact_type="PAIR_CREATED",
            pair_address=normalized.pair_address,
            token0_address=normalized.token0_address,
            token1_address=normalized.token1_address,
            dex=normalized.dex,
        )
        fact_type = FactType.PAIR_CREATED

        return self._build_fact(
            normalized.tx_hash,
            normalized.log_index,
            normalized.block_number,
            normalized.block_hash,
            normalized.event_time,
            collected.observed_at,
            fact_type,
            payload,
        )

    def _process_swap(self, collected: CollectedLog) -> BlockchainFact:
        normalized = normalize_swap(collected)

        payload = SwapExecutedPayload(
            fact_type="SWAP_EXECUTED",
            pool_address=normalized.pool_address,
            sender=normalized.sender,
            recipient=normalized.recipient,
            amount0_in=normalized.amount0_in,
            amount1_in=normalized.amount1_in,
            amount0_out=normalized.amount0_out,
            amount1_out=normalized.amount1_out,
        )
        fact_type = FactType.SWAP_EXECUTED

        return self._build_fact(
            normalized.tx_hash,
            normalized.log_index,
            normalized.block_number,
            normalized.block_hash,
            normalized.event_time,
            collected.observed_at,
            fact_type,
            payload,
        )

    def _process_liquidity(self, collected: CollectedLog) -> BlockchainFact:
        normalized = normalize_liquidity(collected)
        topic0 = collected.raw_log.topics[0]

        # Mint → LIQUIDITY_ADDED, Burn → LIQUIDITY_REMOVED.
        # Both amounts are positive magnitudes; direction comes from
        # fact_type (DOC-012 § B.1).
        liquidity_payload: LiquidityAddedPayload | LiquidityRemovedPayload
        if topic0 == MINT_TOPIC:
            liquidity_payload = LiquidityAddedPayload(
                fact_type="LIQUIDITY_ADDED",
                pool_address=normalized.pool_address,
                provider=normalized.provider,
                amount0=normalized.amount0,
                amount1=normalized.amount1,
                liquidity_delta=normalized.amount0,  # placeholder; see note
            )
            fact_type = FactType.LIQUIDITY_ADDED
        else:
            liquidity_payload = LiquidityRemovedPayload(
                fact_type="LIQUIDITY_REMOVED",
                pool_address=normalized.pool_address,
                provider=normalized.provider,
                amount0=normalized.amount0,
                amount1=normalized.amount1,
                liquidity_delta=normalized.amount0,  # placeholder; see note
            )
            fact_type = FactType.LIQUIDITY_REMOVED

        return self._build_fact(
            normalized.tx_hash,
            normalized.log_index,
            normalized.block_number,
            normalized.block_hash,
            normalized.event_time,
            collected.observed_at,
            fact_type,
            liquidity_payload,
        )

    def _build_fact(
        self,
        tx_hash: str,
        log_index: int,
        block_number: int,
        block_hash: str,
        event_time: datetime,
        observed_at: datetime,
        fact_type: FactType,
        payload: PairCreatedPayload
        | SwapExecutedPayload
        | LiquidityAddedPayload
        | LiquidityRemovedPayload,
    ) -> BlockchainFact:
        # DOC-012 § Modeling the discriminated payload: fact_type and
        # payload.fact_type are intentionally the same value in two places;
        # enforcing their sync is THIS file's job, not the schema's.
        if fact_type.value != payload.fact_type:
            raise DomainValidationError("fact_type/payload.fact_type sync violated")

        fact = BlockchainFact(
            schema_version=SCHEMA_VERSION,
            # Natural key, ':'-safe for fact_id (DOC-012 § Composite ID
            # Delimiter): chain_id and log_index are bare ints, tx_hash is
            # 0x-hex without colons.
            fact_id=f"{self._chain_id}:{tx_hash}:{log_index}",
            chain_id=self._chain_id,
            fact_type=fact_type,
            block_number=block_number,
            block_hash=block_hash,
            tx_hash=tx_hash,
            log_index=log_index,
            event_time=event_time,
            observed_at=observed_at,
            ingested_at=self._clock(),
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
