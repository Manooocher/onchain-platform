"""Unit tests: pool classification (TD-1 domain-aware liquidity).

Verifies USDC / WETH / USDT / DAI / exotic pool detection per the Base-chain
quote-token distribution (roughly USDC ~30%, WETH ~60%, exotic ~10%).
"""

from onchain_platform.analytics.pool_classifier import (
    QuoteTokenType,
    classify_pool,
)

# Canonical addresses (Base chain).
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDT = "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2"
DAI = "0x50c5725949A6f0C72e6C4a641F24049A917DB0Cb"
WETH = "0x4200000000000000000000000000000000000006"
TOKEN_A = "0x1111111111111111111111111111111111111111"
TOKEN_B = "0x2222222222222222222222222222222222222222"
POOL = "0x9999999999999999999999999999999999999999"


def _classify(t0: str, t1: str):
    return classify_pool(POOL, t0, t1)


def test_usdc_pool_classified() -> None:
    c = _classify(TOKEN_A, USDC)
    assert c.quote_token_type == QuoteTokenType.USDC
    assert c.quote_token_address == USDC
    assert c.is_stablecoin_pool is True


def test_usdc_pool_inverted_order() -> None:
    c = _classify(USDC, TOKEN_A)
    assert c.quote_token_type == QuoteTokenType.USDC
    assert c.is_stablecoin_pool is True


def test_weth_pool_classified() -> None:
    c = _classify(TOKEN_A, WETH)
    assert c.quote_token_type == QuoteTokenType.WETH
    assert c.quote_token_address == WETH
    assert c.is_stablecoin_pool is False


def test_weth_usdc_pool_prefers_stablecoin_quote() -> None:
    # WETH/USDC pool: the stablecoin leg is the USD quote.
    c = _classify(WETH, USDC)
    assert c.is_stablecoin_pool is True
    assert c.quote_token_address == USDC


def test_usdt_and_dai_detected() -> None:
    assert _classify(TOKEN_A, USDT).quote_token_type == QuoteTokenType.STABLECOIN
    assert _classify(DAI, TOKEN_A).quote_token_type == QuoteTokenType.STABLECOIN
    assert _classify(TOKEN_A, USDT).is_stablecoin_pool is True


def test_exotic_token_token_pool() -> None:
    c = _classify(TOKEN_A, TOKEN_B)
    assert c.quote_token_type == QuoteTokenType.OTHER
    assert c.is_stablecoin_pool is False


def test_lowercase_inputs_classified() -> None:
    # Inputs may be lowercase; classification must still work.
    c = classify_pool(POOL.lower(), TOKEN_A.lower(), WETH.lower())
    assert c.quote_token_type == QuoteTokenType.WETH
