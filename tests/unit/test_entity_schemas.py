"""Unit tests: entity schemas + Canonical ID construction (DOC-012 Part A).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from onchain_platform.domain.entities.liquidity_pool import LiquidityPool
from onchain_platform.domain.entities.metadata import Metadata
from onchain_platform.domain.entities.smart_contract import SmartContract
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.entities.wallet import Wallet
from onchain_platform.domain.enums import ContractType, VerificationStatus
from onchain_platform.domain.ids import (
    pair_canonical_id,
    smart_contract_canonical_id,
    token_canonical_id,
    wallet_canonical_id,
)

CHAIN_ID = 8453
ADDR = "0x4200000000000000000000000000000000000006"  # WETH on Base


def test_token_canonical_id_format() -> None:
    cid = token_canonical_id(CHAIN_ID, ADDR)
    assert cid == f"eip155:{CHAIN_ID}/token:{ADDR}"
    assert cid.startswith("eip155:")
    assert "/token:" in cid


def test_pair_canonical_id_format() -> None:
    cid = pair_canonical_id(CHAIN_ID, ADDR)
    assert cid == f"eip155:{CHAIN_ID}/pair:{ADDR}"


def test_wallet_canonical_id_format() -> None:
    cid = wallet_canonical_id(CHAIN_ID, ADDR)
    assert cid == f"eip155:{CHAIN_ID}/wallet:{ADDR}"


def test_smart_contract_canonical_id_format() -> None:
    cid = smart_contract_canonical_id(CHAIN_ID, ADDR)
    assert cid == f"eip155:{CHAIN_ID}/contract:{ADDR}"


def test_canonical_id_checksums_address() -> None:
    # Lowercase input → checksummed output.
    lower = "0x4200000000000000000000000000000000000006"
    cid = token_canonical_id(CHAIN_ID, lower)
    assert cid == f"eip155:{CHAIN_ID}/token:{ADDR}"


def test_token_round_trip() -> None:
    t = Token(
        canonical_id=token_canonical_id(CHAIN_ID, ADDR),
        chain_id=CHAIN_ID,
        contract_address=ADDR,
        symbol="WETH",
        name="Wrapped Ether",
        decimals=18,
        total_supply="1000000000000000000",
    )
    restored = Token.model_validate(t.model_dump())
    assert restored == t
    assert restored.symbol == "WETH"


def test_token_frozen_rejects_mutation() -> None:
    t = Token(
        canonical_id=token_canonical_id(CHAIN_ID, ADDR),
        chain_id=CHAIN_ID,
        contract_address=ADDR,
    )
    with pytest.raises(ValidationError):
        t.symbol = "CHANGED"  # type: ignore[misc]


def test_trading_pair_round_trip() -> None:
    tp = TradingPair(
        canonical_id=pair_canonical_id(CHAIN_ID, ADDR),
        chain_id=CHAIN_ID,
        dex="uniswap_v2",
        base_token_id=token_canonical_id(CHAIN_ID, ADDR),
        quote_token_id=token_canonical_id(CHAIN_ID, "0x" + "11" * 20),
        pool_address=ADDR,
        creation_block=13_500_004,
        creation_fact_id="8453:0xfc6bbb...:43",
    )
    restored = TradingPair.model_validate(tp.model_dump())
    assert restored == tp


def test_liquidity_pool_round_trip() -> None:
    lp = LiquidityPool(
        canonical_id=pair_canonical_id(CHAIN_ID, ADDR),
        protocol="uniswap_v2",
    )
    restored = LiquidityPool.model_validate(lp.model_dump())
    assert restored == lp
    assert restored.fee_tier_bps is None


def test_wallet_round_trip() -> None:
    w = Wallet(
        canonical_id=wallet_canonical_id(CHAIN_ID, ADDR),
        chain_id=CHAIN_ID,
        address=ADDR,
        first_seen_at=datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC),
    )
    restored = Wallet.model_validate(w.model_dump())
    assert restored == w
    assert restored.tags == []


def test_smart_contract_round_trip() -> None:
    sc = SmartContract(
        canonical_id=smart_contract_canonical_id(CHAIN_ID, ADDR),
        chain_id=CHAIN_ID,
        address=ADDR,
        contract_type=ContractType.ERC20,
    )
    restored = SmartContract.model_validate(sc.model_dump())
    assert restored == sc
    assert restored.contract_type == ContractType.ERC20
    assert restored.is_verified is False


def test_metadata_round_trip() -> None:
    m = Metadata(
        entity_id=token_canonical_id(CHAIN_ID, ADDR),
        last_updated=datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC),
    )
    restored = Metadata.model_validate(m.model_dump())
    assert restored == m
    assert restored.verification_status == VerificationStatus.UNVERIFIED
    assert restored.website is None
    assert restored.social_links == {}
