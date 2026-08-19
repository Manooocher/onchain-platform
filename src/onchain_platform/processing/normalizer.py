"""Normalizer — provider payload → canonical shape (DOC-011 § processing/).

Decodes a collected PAIR_CREATED log into a canonical, provider-independent
intermediate shape. Everything downstream of this file sees only canonical
shapes — never a raw provider log (ADR-006 Principle 7: normalization
happens once; only canonical representations move through the platform).

Malformed input raises DomainValidationError — a business-rule failure after
field-level checks, translated to a PlatformError subclass before crossing
the Capability boundary (DOC-013 § Exception Hierarchy). Never a raw
exception.
"""

from dataclasses import dataclass
from datetime import datetime

from eth_utils.address import to_checksum_address

from onchain_platform.acquisition.collector import CollectedLog
from onchain_platform.domain.exceptions import DomainValidationError

# keccak256("PairCreated(address,address,address,uint256)") — the Uniswap V2
# factory event signature, identical across every V2 fork (Milestone 1
# planning § Open Decisions: factory attribution verified live).
PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"

_ADDRESS_WORD_BYTES = 32
_ADDRESS_BYTES = 20


@dataclass(frozen=True)
class NormalizedPairCreatedEvent:
    """Canonical shape of one decoded PairCreated log.

    All addresses EIP-55 checksummed (DOC-012 § Conventions — schema-level
    validators will double-check this; the normalizer is where checksumming
    actually happens, DOC-013: acquisition/ is allowed the clock and
    crypto-shaped work). event_time is the block timestamp — actual chain
    time (DOC-008 Triple Timestamp Standard).
    """

    pair_address: str
    token0_address: str
    token1_address: str
    block_number: int
    block_hash: str
    tx_hash: str
    log_index: int
    event_time: datetime
    dex: str


def _topic_to_address(topic: str, field_name: str) -> str:
    """Extract and checksum the trailing 20 bytes of a 32-byte topic word."""
    raw = topic.removeprefix("0x")
    if len(raw) != _ADDRESS_WORD_BYTES * 2:
        raise DomainValidationError(
            f"{field_name}: expected a 32-byte topic word, got {len(raw) // 2} bytes"
        )
    address_hex = raw[(_ADDRESS_WORD_BYTES - _ADDRESS_BYTES) * 2 :]
    return to_checksum_address(address_hex)


def normalize_pair_created(collected: CollectedLog) -> NormalizedPairCreatedEvent:
    """Decode one collected PairCreated log into its canonical shape.

    Uniswap V2 ABI: PairCreated(address token0, address token1, address
    pair, uint256 pairIndex). token0/token1 are indexed (topics 1–2); pair
    and pairIndex are in data (two 32-byte words). pairIndex is read but is
    NOT a DOC-012 payload field — it is intentionally dropped here
    (DOC-012 § Purpose: "If a field is not listed here, it does not exist").
    """
    raw = collected.raw_log
    if raw.removed:
        # A removed log was orphaned on the provider side before we ever
        # made it a Fact — it never enters the pipeline (ADR-006: Facts
        # describe reality; removed logs did not survive it).
        raise DomainValidationError(
            f"refusing to normalize a removed log (tx={raw.transaction_hash}, "
            f"logIndex={raw.log_index})"
        )
    if len(raw.topics) < 3:
        raise DomainValidationError(
            f"PairCreated requires 3 topics (signature + token0 + token1), got {len(raw.topics)}"
        )
    if raw.topics[0] != PAIR_CREATED_TOPIC:
        raise DomainValidationError(f"topic0 {raw.topics[0]!r} is not the PairCreated signature")

    # data: word0 = pair address, word1 = pair index (dropped).
    data_hex = raw.data.removeprefix("0x")
    expected_words = 2
    if len(data_hex) != expected_words * _ADDRESS_WORD_BYTES * 2:
        raise DomainValidationError(
            f"PairCreated data must be exactly {expected_words} 32-byte words, "
            f"got {len(data_hex) // 2} bytes"
        )
    pair_word = data_hex[: _ADDRESS_WORD_BYTES * 2]

    return NormalizedPairCreatedEvent(
        pair_address=to_checksum_address(pair_word[(_ADDRESS_WORD_BYTES - _ADDRESS_BYTES) * 2 :]),
        token0_address=_topic_to_address(raw.topics[1], "token0_address"),
        token1_address=_topic_to_address(raw.topics[2], "token1_address"),
        block_number=raw.block_number,
        block_hash=raw.block_hash,
        tx_hash=raw.transaction_hash,
        log_index=raw.log_index,
        # Block timestamp from the block header — the single canonical
        # source (base.py: provider-specific in-log timestamps are ignored).
        event_time=collected.block.timestamp,
        dex=collected.dex,
    )
