"""Canonical Schema factories for tests (DOC-013 § Testing Conventions).

Hand-writing a BlockchainFact(...) literal inside a test is exactly the
place a 0.1 sneaks in where a Decimal("0.1") belongs — reintroducing,
inside the test suite, the precise bug the Financial Precision Principle
(DOC-008) exists to prevent everywhere else. Every builder here defaults
every field correctly.
"""

from datetime import UTC, datetime

from eth_utils.address import to_checksum_address

from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    PairCreatedPayload,
)
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType

# Real addresses captured from Base block 13,500,004 (the Milestone 1 replay
# fixture sample event). Derived through to_checksum_address from the raw
# lowercase hex — exactly what the normalizer does — so a hand-typing mistake
# can never produce an invalid factory (DOC-012 § Conventions).
WETH_BASE = to_checksum_address("0x4200000000000000000000000000000000000006")
SAMPLE_TOKEN1 = to_checksum_address("0x84e42a7ce453f81d421587103af21c261f4d2a16")
SAMPLE_PAIR = to_checksum_address("0xa431e9b572ca4a0ce1ba10812d3a7b1db718a957")
SAMPLE_TX_HASH = "0xfc6bbb0b00fc647da45dd294ca6355f8f687f2c1ca132f0198d13f3796f54fbd"
SAMPLE_BLOCK_HASH = "0xf7688420b215b621c41d64ec128184809fb3249bc1e70a07d8d197d94e821a41"


def pair_created_payload(
    *,
    pair_address: str = SAMPLE_PAIR,
    token0_address: str = WETH_BASE,
    token1_address: str = SAMPLE_TOKEN1,
    dex: str = "uniswap_v2",
) -> PairCreatedPayload:
    return PairCreatedPayload(
        fact_type="PAIR_CREATED",
        pair_address=pair_address,
        token0_address=token0_address,
        token1_address=token1_address,
        dex=dex,
    )


def blockchain_fact(
    *,
    chain_id: int = 8453,
    tx_hash: str = SAMPLE_TX_HASH,
    log_index: int = 43,
    block_number: int = 13_500_004,
    block_hash: str = SAMPLE_BLOCK_HASH,
    event_time: datetime = datetime(2024, 4, 22, 12, 35, 55, tzinfo=UTC),
    observed_at: datetime = datetime(2024, 4, 22, 12, 35, 56, tzinfo=UTC),
    ingested_at: datetime = datetime(2024, 4, 22, 12, 35, 56, tzinfo=UTC),
    confirmation_status: ConfirmationStatus = ConfirmationStatus.PENDING,
    confirmations: int = 0,
    payload: PairCreatedPayload | None = None,
) -> BlockchainFact:
    """A valid PAIR_CREATED BlockchainFact; every field defaulted correctly.

    fact_id is always derived from the components — never passed in by
    callers — so factories can never produce an inconsistent natural key
    (DOC-012 § B.1, ADR-006 § Idempotency).
    """
    return BlockchainFact(
        schema_version="1.0",
        fact_id=f"{chain_id}:{tx_hash}:{log_index}",
        chain_id=chain_id,
        fact_type=FactType.PAIR_CREATED,
        block_number=block_number,
        block_hash=block_hash,
        tx_hash=tx_hash,
        log_index=log_index,
        event_time=event_time,
        observed_at=observed_at,
        ingested_at=ingested_at,
        confirmation_status=confirmation_status,
        confirmations=confirmations,
        payload=payload or pair_created_payload(),
    )


def checksum_address(seed_byte: int) -> str:
    """Deterministic checksummed address from one byte value (tests only)."""
    return to_checksum_address("0x" + format(seed_byte % 256, "02x") * 20)
