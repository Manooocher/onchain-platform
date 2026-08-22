"""Integration tests: Intelligence pipeline (Milestone 7).

Tests run against real infrastructure where applicable (DOC-010 §
Integration Tests). GoPlus API calls are mocked (external dependency).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.ids import pair_canonical_id, token_canonical_id
from onchain_platform.intelligence.insight_generator import generate_insights
from onchain_platform.intelligence.risk_rules import extract_risk_signals
from onchain_platform.persistence.postgres import (
    entity_repositories as entity_repos,
)
from onchain_platform.persistence.postgres import (
    outcomes_insights,
)

CHAIN_ID = 8453
POOL = "0x39f0E675D479088DE08b7f201Ac08e20F899B838"
TOKEN0 = "0x4200000000000000000000000000000000000006"
TOKEN1 = "0x833589FCdbe0E8C5a3c3f0e0b2F5b5a5A5A5a5a5"
PINNED = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)

MOCK_GOPLUS_RESPONSE: dict[str, object] = {
    "is_honeypot": "0",
    "is_open_source": "1",
    "hidden_owner": "0",
    "sell_tax": "0.05",
    "buy_tax": "0.05",
    "holder_count": "1500",
    "is_blacklisted": "0",
    "is_airdrop_scam": "0",
    "fake_token": "0",
}


async def _seed_entities(session: AsyncSession) -> None:
    """Seed Token + TradingPair entities for the test pool."""
    t0 = Token(
        canonical_id=token_canonical_id(CHAIN_ID, TOKEN0),
        chain_id=CHAIN_ID,
        contract_address=TOKEN0,
    )
    t1 = Token(
        canonical_id=token_canonical_id(CHAIN_ID, TOKEN1),
        chain_id=CHAIN_ID,
        contract_address=TOKEN1,
    )
    await entity_repos.save_token(session, t0)
    await entity_repos.save_token(session, t1)
    tp = TradingPair(
        canonical_id=pair_canonical_id(CHAIN_ID, POOL),
        chain_id=CHAIN_ID,
        dex="uniswap_v2",
        base_token_id=token_canonical_id(CHAIN_ID, TOKEN0),
        quote_token_id=token_canonical_id(CHAIN_ID, TOKEN1),
        pool_address=POOL,
        creation_block=13_500_004,
        creation_fact_id=f"{CHAIN_ID}:0x{'aa' * 32}:0",
    )
    await entity_repos.save_trading_pair(session, tp)


async def test_end_to_end_risk_read(
    pg_engine: AsyncEngine,
    clean_entities: Callable[[], Awaitable[None]],
    clean_facts: Callable[[], Awaitable[None]],
) -> None:
    """Full pipeline: GoPlus response → risk signals → insights persisted."""
    await clean_entities()
    await clean_facts()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await _seed_entities(session)

    # Extract risk signals from mock GoPlus response.
    signals = extract_risk_signals(MOCK_GOPLUS_RESPONSE)
    assert signals.risk_score < 0.5  # clean token
    assert signals.is_honeypot == "0"

    # Generate insights.
    entity_id = pair_canonical_id(CHAIN_ID, POOL)
    insights = generate_insights(entity_id, signals, generated_at=PINNED)

    # Clean token → no high-risk insights.
    high_insights = [i for i in insights if i.importance.value == "HIGH"]
    assert len(high_insights) == 0

    # Persist insights.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        for insight in insights:
            await outcomes_insights.save_insight(session, insight)

    # Verify persisted.
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        loaded = await outcomes_insights.list_insights_for_entity(session, entity_id)
    assert len(loaded) == len(insights)


async def test_honeypot_generates_high_importance_insight(
    pg_engine: AsyncEngine,
    clean_entities: Callable[[], Awaitable[None]],
    clean_facts: Callable[[], Awaitable[None]],
) -> None:
    """Honeypot token → HIGH importance insight."""
    await clean_entities()
    await clean_facts()

    goplus_response: dict[str, object] = {"is_honeypot": "1", "sell_tax": "1.0"}
    signals = extract_risk_signals(goplus_response)
    assert signals.risk_score == 1.0

    entity_id = pair_canonical_id(CHAIN_ID, POOL)
    insights = generate_insights(entity_id, signals, generated_at=PINNED)

    honeypot = [i for i in insights if i.insight_type == "HoneypotDetected"]
    assert len(honeypot) == 1
    assert honeypot[0].importance.value == "HIGH"


async def test_graceful_degradation_on_goplus_failure(
    pg_engine: AsyncEngine,
    clean_entities: Callable[[], Awaitable[None]],
    clean_facts: Callable[[], Awaitable[None]],
) -> None:
    """GoPlus API failure → no insight generated, no crash."""
    await clean_entities()
    await clean_facts()

    # Simulate GoPlus returning None (no data).
    signals = extract_risk_signals({})
    assert signals.risk_score == 0.0
    assert signals.risk_indicators == []

    entity_id = pair_canonical_id(CHAIN_ID, POOL)
    insights = generate_insights(entity_id, signals, generated_at=PINNED)
    assert len(insights) == 0  # no insights for clean/unknown token
