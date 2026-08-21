"""Normalizer — provider payload → canonical shape (DOC-011 § processing/).

Decodes collected logs into canonical, provider-independent intermediate
shapes. Everything downstream of this file sees only canonical shapes —
never a raw provider log (ADR-006 Principle 7: normalization happens once;
only canonical representations move through the platform).

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

# keccak256("Swap(address,uint256,uint256,uint256,uint256,address)") — the
# Uniswap V2 Swap event signature. V3 has a different signature
# (0xc42079f9...) — the normalizer rejects unknown topics with
# DomainValidationError (DOC-012 § Known future extension: V3 is deferred).
SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

# keccak256("Mint(address,uint256,uint256)") — Uniswap V2 Mint event.
# Mint(address indexed sender, uint256 amount0, uint256 amount1).
MINT_TOPIC = "0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"

# keccak256("Burn(address,uint256,uint256)") — Uniswap V2 Burn event.
# Burn(address indexed sender, uint256 amount0, uint256 amount1).
BURN_TOPIC = "0x49995e5dd6158cf69ad3e9777c46755a1a826a446c6416992167462dad033b2a"

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


@dataclass(frozen=True)
class NormalizedSwapEvent:
    """Canonical shape of one decoded V2 Swap log.

    All amounts are decimal strings — raw on-chain integers in the smallest
    denomination (DOC-008 § Token Amount). Never float, never a native JSON
    number (DOC-008 § Financial Precision Principle).

    V2 Swap ABI: Swap(address indexed sender, uint256 amount0In, uint256
    amount1In, uint256 amount0Out, uint256 amount1Out, address indexed to).
    sender and to are indexed (topics 1–2); amounts are in data (four
    32-byte words).
    """

    pool_address: str
    sender: str
    recipient: str
    amount0_in: str
    amount1_in: str
    amount0_out: str
    amount1_out: str
    block_number: int
    block_hash: str
    tx_hash: str
    log_index: int
    event_time: datetime
    dex: str


@dataclass(frozen=True)
class NormalizedLiquidityEvent:
    """Canonical shape of one decoded Mint or Burn log.

    V2 Mint/Burn ABI: Mint(address indexed sender, uint256 amount0,
    uint256 amount1) / Burn(address indexed sender, uint256 amount0,
    uint256 amount1). sender is indexed (topic 1); amounts are in data
    (two 32-byte words).

    Both amounts are positive magnitudes; direction comes from fact_type
    (DOC-012 § B.1: "liquidity_delta is always a positive magnitude.
    Direction comes exclusively from fact_type"). All amounts are decimal
    strings — raw on-chain integers (DOC-008 § Token Amount).
    """

    pool_address: str
    provider: str  # sender address, EIP-55 checksummed
    amount0: str
    amount1: str
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


def _data_word_to_address(data_hex: str, word_index: int, field_name: str) -> str:
    """Extract and checksum an address from a 32-byte data word."""
    start = word_index * _ADDRESS_WORD_BYTES * 2
    word = data_hex[start : start + _ADDRESS_WORD_BYTES * 2]
    address_hex = word[(_ADDRESS_WORD_BYTES - _ADDRESS_BYTES) * 2 :]
    return to_checksum_address(address_hex)


def _data_word_to_int(data_hex: str, word_index: int) -> int:
    """Extract an integer from a 32-byte data word."""
    start = word_index * _ADDRESS_WORD_BYTES * 2
    word = data_hex[start : start + _ADDRESS_WORD_BYTES * 2]
    return int(word, 16)


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


def normalize_swap(collected: CollectedLog) -> NormalizedSwapEvent:
    """Decode one collected V2 Swap log into its canonical shape.

    V2 Swap ABI: Swap(address indexed sender, uint256 amount0In, uint256
    amount1In, uint256 amount0Out, uint256 amount1Out, address indexed to).
    - topics[0] = event signature
    - topics[1] = sender (indexed)
    - topics[2] = to/recipient (indexed)
    - data = amount0In + amount1In + amount0Out + amount1Out (4 × 32 bytes)

    All amounts are decimal strings — raw on-chain integers (DOC-008 §
    Token Amount). Never float (DOC-008 § Financial Precision Principle).

    Validation: at least one _in > 0 AND at least one _out > 0 (a swap
    with all zeros is nonsensical). Also: exactly one _in field is > 0
    (V2 swaps are one-directional — you don't send both tokens in).
    """
    raw = collected.raw_log
    if raw.removed:
        raise DomainValidationError(
            f"refusing to normalize a removed log (tx={raw.transaction_hash}, "
            f"logIndex={raw.log_index})"
        )
    if len(raw.topics) < 3:
        raise DomainValidationError(
            f"Swap requires 3 topics (signature + sender + to), got {len(raw.topics)}"
        )
    if raw.topics[0] != SWAP_TOPIC:
        raise DomainValidationError(f"topic0 {raw.topics[0]!r} is not the V2 Swap signature")

    # data: 4 × 32-byte words = amount0In, amount1In, amount0Out, amount1Out.
    data_hex = raw.data.removeprefix("0x")
    expected_bytes = 4 * _ADDRESS_WORD_BYTES
    if len(data_hex) != expected_bytes * 2:
        raise DomainValidationError(
            f"Swap data must be exactly 4 32-byte words, got {len(data_hex) // 2} bytes"
        )

    amount0_in = _data_word_to_int(data_hex, 0)
    amount1_in = _data_word_to_int(data_hex, 1)
    amount0_out = _data_word_to_int(data_hex, 2)
    amount1_out = _data_word_to_int(data_hex, 3)

    # Validation: at least one _in and one _out must be > 0.
    if amount0_in == 0 and amount1_in == 0:
        raise DomainValidationError(
            f"Swap has both amount0_in=0 and amount1_in=0 "
            f"(tx={raw.transaction_hash}, logIndex={raw.log_index})"
        )
    if amount0_out == 0 and amount1_out == 0:
        raise DomainValidationError(
            f"Swap has both amount0_out=0 and amount1_out=0 "
            f"(tx={raw.transaction_hash}, logIndex={raw.log_index})"
        )
    # V2 swaps are one-directional: exactly one _in field is > 0.
    if amount0_in > 0 and amount1_in > 0:
        raise DomainValidationError(
            f"Swap has both amount0_in={amount0_in} and amount1_in={amount1_in} "
            f"(tx={raw.transaction_hash}, logIndex={raw.log_index})"
        )

    # pool_address is the emitting contract (raw.address).
    pool_address = to_checksum_address(raw.address)

    return NormalizedSwapEvent(
        pool_address=pool_address,
        sender=_topic_to_address(raw.topics[1], "sender"),
        recipient=_topic_to_address(raw.topics[2], "recipient"),
        # Amounts as decimal strings — raw on-chain integers (DOC-008 §
        # Token Amount). Never float.
        amount0_in=str(amount0_in),
        amount1_in=str(amount1_in),
        amount0_out=str(amount0_out),
        amount1_out=str(amount1_out),
        block_number=raw.block_number,
        block_hash=raw.block_hash,
        tx_hash=raw.transaction_hash,
        log_index=raw.log_index,
        event_time=collected.block.timestamp,
        dex=collected.dex,
    )


def normalize_liquidity(collected: CollectedLog) -> NormalizedLiquidityEvent:
    """Decode one collected Mint or Burn log into its canonical shape.

    V2 Mint/Burn ABI: Mint(address indexed sender, uint256 amount0,
    uint256 amount1) / Burn(address indexed sender, uint256 amount0,
    uint256 amount1). sender is indexed (topic 1); amounts are in data
    (two 32-byte words).

    Both amounts are positive magnitudes; direction comes from fact_type
    (DOC-012 § B.1). All amounts are decimal strings — raw on-chain
    integers (DOC-008 § Token Amount). Never float.
    """
    raw = collected.raw_log
    if raw.removed:
        raise DomainValidationError(
            f"refusing to normalize a removed log (tx={raw.transaction_hash}, "
            f"logIndex={raw.log_index})"
        )
    if len(raw.topics) < 2:
        raise DomainValidationError(
            f"Mint/Burn requires 2 topics (signature + sender), got {len(raw.topics)}"
        )
    topic0 = raw.topics[0]
    if topic0 not in (MINT_TOPIC, BURN_TOPIC):
        raise DomainValidationError(f"topic0 {topic0!r} is not a V2 Mint or Burn signature")

    # data: 2 x 32-byte words = amount0, amount1.
    data_hex = raw.data.removeprefix("0x")
    if len(data_hex) != 2 * _ADDRESS_WORD_BYTES * 2:
        raise DomainValidationError(
            f"Mint/Burn data must be exactly 2 32-byte words, got {len(data_hex) // 2} bytes"
        )

    amount0 = _data_word_to_int(data_hex, 0)
    amount1 = _data_word_to_int(data_hex, 1)

    # Both amounts must be > 0 (DOC-012 B.1: "always positive magnitudes").
    if amount0 <= 0 or amount1 <= 0:
        raise DomainValidationError(
            f"Mint/Burn amounts must be positive: amount0={amount0}, amount1={amount1} "
            f"(tx={raw.transaction_hash}, logIndex={raw.log_index})"
        )

    return NormalizedLiquidityEvent(
        pool_address=to_checksum_address(raw.address),
        provider=_topic_to_address(raw.topics[1], "provider"),
        amount0=str(amount0),
        amount1=str(amount1),
        block_number=raw.block_number,
        block_hash=raw.block_hash,
        tx_hash=raw.transaction_hash,
        log_index=raw.log_index,
        event_time=collected.block.timestamp,
        dex=collected.dex,
    )
