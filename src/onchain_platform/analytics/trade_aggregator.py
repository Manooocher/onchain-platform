"""Trade Aggregator — finalized SWAP_EXECUTED facts → Market Bars (OHLCV)
(DOC-006 § Market Data Pipeline, DOC-012 § B.3).

Derived exclusively from FINALIZED SWAP_EXECUTED facts (DOC-006 — never
from ObservationSnapshot). This is the piece that was rewritten twice
during design (ImplementationPlan § Milestone 3).

All intermediate math uses Decimal (DOC-008 § Financial Precision
Principle). A single float conversion invalidates the entire milestone.

Price is always computed as token1 per token0 (DOC-012 § B.2
StateProjection: 'price | str | token1 per token0, Decimal-as-string').

Buy/sell volume convention: token0 = base, token1 = quote.
- amount0_in > 0 → user sold base (token0) → sell_volume += amount0_in
- amount1_in > 0 → user bought base (token0) → buy_volume += amount0_out

Bar bucketing: epoch-based modulo arithmetic (bar_start = event_time -
(event_time % interval_seconds)). Deterministic, timezone-independent
(UTC).

Reconstruction predicate (DOC-012 § B.3): a bar's contents are exactly
the set of SWAP_EXECUTED facts for pair_id where bar_start_time <=
event_time < bar_end_time, restricted to FINALIZED facts. source_fact_range
records what the predicate matched, for audit — it is NOT the authoritative
definition.

On reorg (DOC-012 § B.3): if any fact inside an already-computed bar's
source_fact_range transitions to ORPHANED, the entire bar is recomputed
from the predicate — never patched incrementally.
"""

from datetime import UTC, datetime
from decimal import Decimal, getcontext

import structlog

from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import BarInterval
from onchain_platform.domain.schemas.market_bar import MarketBar

# Match NUMERIC(78,0) for uint256 inputs (DOC-014 § Type Mapping Rules).
getcontext().prec = 78

logger = structlog.get_logger(__name__)


def compute_pair_id(chain_id: int, pool_address: str) -> str:
    """Construct pair_id from chain_id and pool_address.

    Deterministic, no entity resolution needed (Milestone 4). Format:
    eip155:<chain_id>/pair:<address> (DOC-008 § Canonical ID).
    """
    return f"eip155:{chain_id}/pair:{pool_address}"


def bucket_start(event_time: datetime, interval: BarInterval) -> datetime:
    """Epoch-based modulo arithmetic for bar bucketing.

    bar_start = event_time - (event_time % interval_seconds).
    Deterministic, timezone-independent (UTC epoch arithmetic).
    """
    ts = int(event_time.timestamp())
    interval_sec = interval.seconds
    bucket_ts = ts - (ts % interval_sec)
    return datetime.fromtimestamp(bucket_ts, tz=UTC)


def aggregate_swaps_to_bar(
    facts: list[BlockchainFact],
    pair_id: str,
    chain_id: int,
    interval: BarInterval,
    bar_start: datetime,
    computed_at: datetime,
) -> MarketBar | None:
    """Aggregate a list of FINALIZED SWAP_EXECUTED facts into one MarketBar.

    Returns None if facts is empty (no bar produced for empty windows).

    All intermediate math uses Decimal (DOC-008 § Financial Precision).
    Price is always token1 per token0 (DOC-012 § B.2).

    facts must already be filtered to the correct pair_id, time window,
    and FINALIZED status. This function is a pure aggregation — it does
    not query the database.
    """
    if not facts:
        return None

    # Sort by (block_number, log_index) for deterministic OHLCV ordering.
    sorted_facts = sorted(facts, key=lambda f: (f.block_number, f.log_index))

    prices: list[Decimal] = []
    volume_base = Decimal(0)
    volume_quote = Decimal(0)
    buy_volume = Decimal(0)
    sell_volume = Decimal(0)

    for fact in sorted_facts:
        payload = fact.payload
        if not isinstance(payload, SwapExecutedPayload):
            continue

        a0_in = Decimal(payload.amount0_in)
        a1_in = Decimal(payload.amount1_in)
        a0_out = Decimal(payload.amount0_out)
        a1_out = Decimal(payload.amount1_out)

        # Price: always token1 per token0 (DOC-012 § B.2).
        if a0_in > 0:
            # User gave token0, got token1 → price = amount1_out / amount0_in
            price = a1_out / a0_in
            # Sell: user sold base (token0)
            sell_volume += a0_in
        else:
            # User gave token1, got token0 → price = amount1_in / amount0_out
            price = a1_in / a0_out
            # Buy: user bought base (token0)
            buy_volume += a0_out

        prices.append(price)

        # Total volume in each token.
        volume_base += a0_in + a0_out
        volume_quote += a1_in + a1_out

    if not prices:
        return None

    # OHLCV.
    open_price = prices[0]
    high_price = max(prices)
    low_price = min(prices)
    close_price = prices[-1]

    # VWAP: Σ(price × volume_base_i) / Σ(volume_base_i).
    # For simplicity, use per-swap volume_base contribution.
    vwap_num = Decimal(0)
    vwap_den = Decimal(0)
    for i, fact in enumerate(sorted_facts):
        payload = fact.payload
        if not isinstance(payload, SwapExecutedPayload):
            continue
        a0_in = Decimal(payload.amount0_in)
        a0_out = Decimal(payload.amount0_out)
        swap_vol = a0_in + a0_out
        vwap_num += prices[i] * swap_vol
        vwap_den += swap_vol

    vwap = (vwap_num / vwap_den) if vwap_den > 0 else Decimal(0)

    # source_fact_range: (first_fact_id, last_fact_id) ordered by
    # (block_number, log_index). For audit only — the reconstruction
    # predicate is the authoritative definition (DOC-012 § B.3).
    source_fact_range = (sorted_facts[0].fact_id, sorted_facts[-1].fact_id)

    return MarketBar.create(
        pair_id=pair_id,
        chain_id=chain_id,
        interval=interval,
        bar_start_time=bar_start,
        open_=str(open_price),
        high=str(high_price),
        low=str(low_price),
        close=str(close_price),
        volume_base=str(volume_base),
        volume_quote=str(volume_quote),
        trade_count=len(sorted_facts),
        vwap=str(vwap),
        buy_volume=str(buy_volume),
        sell_volume=str(sell_volume),
        source_fact_range=source_fact_range,
        is_provisional=False,  # M3: FINALIZED only
        computed_at=computed_at,
    )
