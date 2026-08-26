"""Pool classification (Milestone TD-1 domain-aware liquidity USD).

Real Base-chain DEX pools are dominated by two quote-token shapes:
- ~60% Token/WETH
- ~30% Token/USDC (or another stablecoin)
- ~10% Token/Token / exotic

Correct liquidity_usd math depends on classifying the pool's quote token:
- Token/USDC pools are stablecoin-denominated (symmetric, reserves are USD).
- Token/WETH pools need an external ETH/USD price.
- Exotic pools have no defensible USD value (liquidity_usd = NULL).

This module is the deterministic, pure classification layer. It identifies the
quote token given the two token addresses of a Uniswap-V2-style pool and the
pool's address. It lives in analytics/ and depends only on domain/ (DOC-011:
analytics may import domain).
"""

from onchain_platform.domain.interfaces.price_oracle import PoolClassification
from onchain_platform.domain.schemas.enums import QuoteTokenType

# Known base-chain token addresses (canonical lowercase form; checksummed at
# classification time). These are well-known, permanent registry entries, not
# ephemeral runtime values.
_STABLECOIN_ADDRESSES_LC: dict[str, QuoteTokenType] = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": QuoteTokenType.USDC,  # USDC
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": QuoteTokenType.STABLECOIN,  # USDT
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": QuoteTokenType.STABLECOIN,  # DAI
}

_WETH_ADDRESS = "0x4200000000000000000000000000000000000006"


def _to_lc(address: str) -> str:
    return address.lower()


def classify_pool(pool_address: str, token0: str, token1: str) -> PoolClassification:
    """Classify a V2 pool by identifying its USD quote token.

    Returns a PoolClassification. Addresses may be checksummed or lowercase;
    output always uses the inputs as-is for pool_address/token0/token1 and the
    identified quote token's original form.

    Strategy:
    - If token0 is WETH and token1 is a stablecoin → quote is the stablecoin.
    - If token0 is a stablecoin and token1 is WETH → quote is WETH (the pool is
      WETH-denominated; the stablecoin leg is the base).
    - If one leg is WETH → that leg is the quote (WETH pool).
    - If one leg is a stablecoin → that leg is the quote (stablecoin pool).
    - Else → exotic (OTHER), quote = token1.
    """
    t0_lc = _to_lc(token0)
    t1_lc = _to_lc(token1)

    tok0_type = _stablecoin_type(t0_lc)
    tok1_type = _stablecoin_type(t1_lc)
    tok0_is_weth = t0_lc == _WETH_ADDRESS
    tok1_is_weth = t1_lc == _WETH_ADDRESS

    # WETH + stablecoin → stablecoin is the USD quote.
    if tok0_is_weth and tok1_type is not None:
        return PoolClassification(
            pool_address=pool_address,
            token0=token0,
            token1=token1,
            quote_token_type=tok1_type,
            quote_token_address=token1,
            is_stablecoin_pool=True,
        )
    if tok1_is_weth and tok0_type is not None:
        return PoolClassification(
            pool_address=pool_address,
            token0=token0,
            token1=token1,
            quote_token_type=tok0_type,
            quote_token_address=token0,
            is_stablecoin_pool=True,
        )

    # One WETH leg → WETH is the quote.
    if tok0_is_weth:
        return PoolClassification(
            pool_address=pool_address,
            token0=token0,
            token1=token1,
            quote_token_type=QuoteTokenType.WETH,
            quote_token_address=token0,
            is_stablecoin_pool=False,
        )
    if tok1_is_weth:
        return PoolClassification(
            pool_address=pool_address,
            token0=token0,
            token1=token1,
            quote_token_type=QuoteTokenType.WETH,
            quote_token_address=token1,
            is_stablecoin_pool=False,
        )

    # One stablecoin leg → stablecoin is the quote.
    if tok0_type is not None:
        return PoolClassification(
            pool_address=pool_address,
            token0=token0,
            token1=token1,
            quote_token_type=tok0_type,
            quote_token_address=token0,
            is_stablecoin_pool=True,
        )
    if tok1_type is not None:
        return PoolClassification(
            pool_address=pool_address,
            token0=token0,
            token1=token1,
            quote_token_type=tok1_type,
            quote_token_address=token1,
            is_stablecoin_pool=True,
        )

    # Two non-WETH, non-stablecoin legs → exotic.
    return PoolClassification(
        pool_address=pool_address,
        token0=token0,
        token1=token1,
        quote_token_type=QuoteTokenType.OTHER,
        quote_token_address=token1,
        is_stablecoin_pool=False,
    )


def _stablecoin_type(lower_address: str) -> QuoteTokenType | None:
    """Return the stablecoin QuoteTokenType for an address, or None."""
    return _STABLECOIN_ADDRESSES_LC.get(lower_address)
