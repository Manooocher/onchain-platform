"""Unit tests: StateProjection + ObservationSnapshot schemas (DOC-012 § B.2,
§ B.3).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from onchain_platform.domain.schemas.observation_snapshot import ObservationSnapshot
from onchain_platform.domain.schemas.state_projection import StateProjection

PINNED = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def test_state_projection_round_trip() -> None:
    sp = StateProjection(
        entity_id="eip155:8453/pair:0xabc",
        chain_id=8453,
        as_of_block=100,
        as_of_fact_id="8453:0xaa:0",
        computed_at=PINNED,
        reserve0="1000000000000000000",
        reserve1="2000000000000000000",
        price="2.0",
    )
    restored = StateProjection.model_validate(sp.model_dump())
    assert restored == sp


def test_state_projection_frozen_rejects_mutation() -> None:
    sp = StateProjection(
        entity_id="eip155:8453/pair:0xabc",
        chain_id=8453,
        as_of_block=100,
        as_of_fact_id="8453:0xaa:0",
        computed_at=PINNED,
        reserve0="1000",
        reserve1="2000",
        price="2.0",
    )
    with pytest.raises(ValidationError):
        sp.reserve0 = "9999"  # type: ignore[misc]


def test_state_projection_negative_reserve_rejected() -> None:
    with pytest.raises(ValidationError, match="non-negative"):
        StateProjection(
            entity_id="eip155:8453/pair:0xabc",
            chain_id=8453,
            as_of_block=100,
            as_of_fact_id="8453:0xaa:0",
            computed_at=PINNED,
            reserve0="-100",
            reserve1="2000",
            price="2.0",
        )


def test_state_projection_non_integer_reserve_rejected() -> None:
    with pytest.raises(ValidationError, match="non-negative integer"):
        StateProjection(
            entity_id="eip155:8453/pair:0xabc",
            chain_id=8453,
            as_of_block=100,
            as_of_fact_id="8453:0xaa:0",
            computed_at=PINNED,
            reserve0="1.5",
            reserve1="2000",
            price="2.0",
        )


def test_observation_snapshot_round_trip() -> None:
    snap = ObservationSnapshot.create(
        entity_id="eip155:8453/pair:0xabc",
        chain_id=8453,
        snapshot_timestamp=PINNED,
        observed_at=PINNED,
        ingested_at=PINNED,
        source="projection_engine:poll:60s",
        reserve0="1000",
        reserve1="2000",
        price="2.0",
    )
    restored = ObservationSnapshot.model_validate(snap.model_dump())
    assert restored == snap
    assert restored.liquidity_usd is None
    assert restored.holder_count is None


def test_observation_snapshot_id_format() -> None:
    snap = ObservationSnapshot.create(
        entity_id="eip155:8453/pair:0xabc",
        chain_id=8453,
        snapshot_timestamp=PINNED,
        observed_at=PINNED,
        ingested_at=PINNED,
        source="projection_engine:poll:60s",
        reserve0="1000",
        reserve1="2000",
        price="2.0",
    )
    # snapshot_id uses '|' delimiter (DOC-012 § Composite ID Delimiter).
    assert "|" in snap.snapshot_id
    parts = snap.snapshot_id.split("|")
    assert len(parts) == 3
    assert parts[0] == "eip155:8453/pair:0xabc"


def test_observation_snapshot_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ObservationSnapshot.create(
            entity_id="eip155:8453/pair:0xabc",
            chain_id=8453,
            snapshot_timestamp=datetime(2024, 1, 1),  # naive
            observed_at=PINNED,
            ingested_at=PINNED,
            source="test",
            reserve0="1000",
            reserve1="2000",
            price="2.0",
        )
