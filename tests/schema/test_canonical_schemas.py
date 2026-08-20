"""Schema Validation Tests (DOC-010 § Testing) for BlockchainFact.

Property-based tests via hypothesis, covering version-boundary and
malformed-input edge cases before they can reach the Fact Processor
(DOC-010 § Schema Validation Tests). Naming: test_<unit>_<scenario>_<
expected_outcome> (DOC-013 § Testing Conventions).
"""

from datetime import UTC, datetime

import pytest
from eth_utils.address import to_checksum_address
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from onchain_platform.domain.schemas.blockchain_fact import BlockchainFact, PairCreatedPayload
from tests.factories.blockchain_fact import (
    SAMPLE_BLOCK_HASH,
    SAMPLE_TX_HASH,
    blockchain_fact,
    checksum_address,
)

# ---------------------------------------------------------------------------
# Strategies (schema-valid components only)
# ---------------------------------------------------------------------------

_checksummed_addresses = st.binary(min_size=20, max_size=20).map(
    lambda b: to_checksum_address(b.hex())
)
_lowercase_hashes = st.binary(min_size=32, max_size=32).map(lambda b: "0x" + b.hex())
_utc_datetimes = st.datetimes(
    min_value=datetime(2020, 1, 1), max_value=datetime(2030, 1, 1), timezones=st.just(UTC)
)
_token_amounts = st.integers(min_value=0, max_value=2**256 - 1).map(str)


@st.composite
def blockchain_facts(draw: st.DrawFn) -> BlockchainFact:
    chain_id = draw(st.integers(min_value=1, max_value=2**32))
    tx_hash = draw(_lowercase_hashes)
    log_index = draw(st.integers(min_value=0, max_value=10_000))
    return blockchain_fact(
        chain_id=chain_id,
        tx_hash=tx_hash,
        log_index=log_index,
        block_number=draw(st.integers(min_value=0, max_value=2**63 - 1)),
        block_hash=draw(_lowercase_hashes),
        event_time=draw(_utc_datetimes),
        observed_at=draw(_utc_datetimes),
        ingested_at=draw(_utc_datetimes),
        confirmations=draw(st.integers(min_value=0, max_value=10_000)),
    )


# ---------------------------------------------------------------------------
# Round-trip: byte-identical for every str field (DOC-010 § Replay Tests —
# Decimal/String fields are asserted byte-identical, zero tolerance).
# ---------------------------------------------------------------------------


@given(fact=blockchain_facts())
@settings(max_examples=200)
def test_blockchain_fact_round_trip_is_byte_identical(fact: BlockchainFact) -> None:
    restored = BlockchainFact.model_validate(fact.model_dump())
    assert restored == fact
    assert restored.fact_id == fact.fact_id
    assert restored.tx_hash == fact.tx_hash
    assert restored.block_hash == fact.block_hash
    assert restored.payload == fact.payload


@given(fact=blockchain_facts())
@settings(max_examples=50)
def test_blockchain_fact_json_round_trip_preserves_z_suffix(fact: BlockchainFact) -> None:
    dumped = fact.model_dump(mode="json")
    assert str(dumped["event_time"]).endswith("Z")
    restored = BlockchainFact.model_validate_json(fact.model_dump_json())
    assert restored.event_time == fact.event_time


# ---------------------------------------------------------------------------
# Malformed-input rejection — these must fail BEFORE the Fact Processor ever
# sees them (DOC-010 § Schema Validation Tests).
# ---------------------------------------------------------------------------


def test_blockchain_fact_naive_datetime_rejected_not_warning() -> None:
    # DOC-012 § Conventions: "A naive datetime is a validation error, not a
    # warning." Pydantic v2 accepts naive datetimes by default — the explicit
    # validator must reject them.
    naive = datetime(2024, 4, 22, 12, 35, 55)  # no tzinfo
    with pytest.raises(ValidationError, match="timezone-aware"):
        blockchain_fact(event_time=naive)


def test_blockchain_fact_non_checksummed_address_rejected() -> None:
    # DOC-012 § Conventions: EIP-55 checksumming is a schema-level validator.
    lowered = "0x84e42a7ce453f81d421587103af21c261f4d2a16"
    with pytest.raises(ValidationError, match="EIP-55"):
        from tests.factories.blockchain_fact import pair_created_payload

        pair_created_payload(token1_address=lowered)


def test_blockchain_fact_fact_id_component_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="does not match its components"):
        BlockchainFact(
            schema_version="1.0",
            fact_id="9999:" + SAMPLE_TX_HASH + ":43",  # wrong chain_id
            chain_id=8453,
            fact_type="PAIR_CREATED",  # type: ignore[arg-type]
            block_number=13_500_004,
            block_hash=SAMPLE_BLOCK_HASH,
            tx_hash=SAMPLE_TX_HASH,
            log_index=43,
            event_time=datetime(2024, 4, 22, tzinfo=UTC),
            observed_at=datetime(2024, 4, 22, tzinfo=UTC),
            ingested_at=datetime(2024, 4, 22, tzinfo=UTC),
            confirmation_status="PENDING",  # type: ignore[arg-type]
            confirmations=0,
            payload=PairCreatedPayload(
                fact_type="PAIR_CREATED",
                pair_address=checksum_address(1),
                token0_address=checksum_address(2),
                token1_address=checksum_address(3),
                dex="uniswap_v2",
            ),
        )


def test_blockchain_fact_uppercase_hash_rejected() -> None:
    # Canonical JSON-RPC form is lowercase (DOC-014 VARCHAR(66) hashes; the
    # normalizer lowercases before construction — schema pins the form).
    with pytest.raises(ValidationError, match="lowercase hex"):
        blockchain_fact(tx_hash=SAMPLE_TX_HASH.upper())


def test_blockchain_fact_frozen_rejects_mutation() -> None:
    # DOC-013 § Immutability: state change is model_copy(update=...), never
    # mutation.
    fact = blockchain_fact()
    with pytest.raises(ValidationError):
        fact.confirmations = 5  # type: ignore[misc]
    # The sanctioned transition path works:
    advanced = fact.model_copy(update={"confirmations": 1})
    assert advanced.confirmations == 1
    assert fact.confirmations == 0


def test_blockchain_fact_discriminator_dispatches_payload() -> None:
    # DOC-012 § Modeling the discriminated payload: payload.fact_type selects
    # the union member; an unknown fact_type must fail validation, not
    # silently parse into the wrong shape. (model_validate, not model_copy —
    # copy deliberately skips validation.)
    data = blockchain_fact().model_dump()
    data["payload"] = {
        "fact_type": "NOT_A_FACT_TYPE",
        "pair_address": checksum_address(1),
        "token0_address": checksum_address(2),
        "token1_address": checksum_address(3),
        "dex": "uniswap_v2",
    }
    with pytest.raises(ValidationError):
        BlockchainFact.model_validate(data)


# ---------------------------------------------------------------------------
# Checkpoint (DOC-012 § B.0)
# ---------------------------------------------------------------------------


def test_checkpoint_round_trip_is_byte_identical() -> None:
    from onchain_platform.domain.schemas.checkpoint import Checkpoint

    cp = Checkpoint(
        chain_id=8453,
        last_finalized_block=13_500_000,
        last_finalized_at=datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC),
    )
    restored = Checkpoint.model_validate(cp.model_dump())
    assert restored == cp


def test_checkpoint_frozen_rejects_mutation() -> None:
    from onchain_platform.domain.schemas.checkpoint import Checkpoint

    cp = Checkpoint(
        chain_id=8453,
        last_finalized_block=100,
        last_finalized_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        cp.last_finalized_block = 200  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ChainReorgEvent (DOC-012 § B.5)
# ---------------------------------------------------------------------------


def test_chain_reorg_event_round_trip_is_byte_identical() -> None:
    from onchain_platform.domain.schemas.chain_reorg_event import ChainReorgEvent

    event = ChainReorgEvent.create(
        chain_id=8453,
        fork_block_number=13_500_005,
        orphaned_block_range=(13_500_005, 13_500_010),
        new_canonical_head_hash="0x" + "ab" * 32,
        depth=6,
        detected_at=datetime(2024, 4, 22, 13, 0, 0, tzinfo=UTC),
    )
    restored = ChainReorgEvent.model_validate(event.model_dump())
    assert restored == event
    assert restored.depth == 6
    assert restored.fork_block_number == 13_500_005


def test_chain_reorg_event_naive_detected_at_rejected() -> None:
    from onchain_platform.domain.schemas.chain_reorg_event import ChainReorgEvent

    with pytest.raises(ValidationError, match="timezone-aware"):
        ChainReorgEvent.create(
            chain_id=8453,
            fork_block_number=100,
            orphaned_block_range=(100, 105),
            new_canonical_head_hash="0x" + "ab" * 32,
            depth=5,
            detected_at=datetime(2024, 1, 1),  # naive
        )


def test_chain_reorg_event_reversed_range_rejected() -> None:
    from onchain_platform.domain.schemas.chain_reorg_event import ChainReorgEvent

    with pytest.raises(ValidationError, match="first <= last"):
        ChainReorgEvent.create(
            chain_id=8453,
            fork_block_number=100,
            orphaned_block_range=(105, 100),  # reversed
            new_canonical_head_hash="0x" + "ab" * 32,
            depth=5,
        )


def test_chain_reorg_event_frozen_rejects_mutation() -> None:
    from onchain_platform.domain.schemas.chain_reorg_event import ChainReorgEvent

    event = ChainReorgEvent.create(
        chain_id=8453,
        fork_block_number=100,
        orphaned_block_range=(100, 105),
        new_canonical_head_hash="0x" + "ab" * 32,
        depth=5,
    )
    with pytest.raises(ValidationError):
        event.depth = 10  # type: ignore[misc]
