"""Unit tests: entity resolution (DOC-011 § domain_management/).

Naming: test_<unit>_<scenario>_<expected_outcome> (DOC-013 § Testing
Conventions). Entity resolution idempotency is the highest-correctness
requirement (ImplementationPlan § Milestone 4 constraints).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onchain_platform.domain.ids import (
    pair_canonical_id,
    token_canonical_id,
    wallet_canonical_id,
)
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    PairCreatedPayload,
    SwapExecutedPayload,
)
from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType
from onchain_platform.domain_management import entity_resolution
from onchain_platform.persistence.postgres import entity_repositories as repos

CHAIN_ID = 8453
POOL = "0x39f0E675D479088DE08b7f201Ac08e20F899B838"
TOKEN0 = "0x4200000000000000000000000000000000000006"
TOKEN1 = "0x833589FCdbe0E8C5a3c3f0e0b2F5b5a5A5A5a5a5"
SENDER = "0xeef9027F3b887713D91C4C0965a08d1776859b00"
RECIPIENT = "0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa"
PINNED_TIME = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def _make_pair_created_fact() -> BlockchainFact:
    payload = PairCreatedPayload(
        fact_type="PAIR_CREATED",
        pair_address=POOL,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        dex="uniswap_v2",
    )
    return BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{'aa' * 32}:0",
        chain_id=CHAIN_ID,
        fact_type=FactType.PAIR_CREATED,
        block_number=13_500_004,
        block_hash=f"0x{'bb' * 32}",
        tx_hash=f"0x{'aa' * 32}",
        log_index=0,
        event_time=datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC),
        observed_at=PINNED_TIME,
        ingested_at=PINNED_TIME,
        confirmation_status=ConfirmationStatus.PENDING,
        confirmations=0,
        payload=payload,
    )


def _make_swap_fact() -> BlockchainFact:
    payload = SwapExecutedPayload(
        fact_type="SWAP_EXECUTED",
        pool_address=POOL,
        sender=SENDER,
        recipient=RECIPIENT,
        amount0_in="1000",
        amount1_in="0",
        amount0_out="0",
        amount1_out="5000",
    )
    return BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{'cc' * 32}:1",
        chain_id=CHAIN_ID,
        fact_type=FactType.SWAP_EXECUTED,
        block_number=13_500_005,
        block_hash=f"0x{'dd' * 32}",
        tx_hash=f"0x{'cc' * 32}",
        log_index=1,
        event_time=datetime(2024, 4, 22, 12, 0, 5, tzinfo=UTC),
        observed_at=PINNED_TIME,
        ingested_at=PINNED_TIME,
        confirmation_status=ConfirmationStatus.PENDING,
        confirmations=0,
        payload=payload,
    )


_CleanFn = Callable[[], Awaitable[None]]


async def test_pair_created_creates_tokens_and_pair(
    pg_engine: AsyncEngine, clean_entities: _CleanFn
) -> None:
    await clean_entities()
    fact = _make_pair_created_fact()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_resolution.resolve_from_pair_created(session, fact)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        t0 = await repos.get_token(session, token_canonical_id(CHAIN_ID, TOKEN0))
        t1 = await repos.get_token(session, token_canonical_id(CHAIN_ID, TOKEN1))
    assert t0 is not None
    assert t0.contract_address == TOKEN0
    assert t0.symbol == "UNKNOWN"
    assert t1 is not None
    assert t1.contract_address == TOKEN1

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        tp = await repos.get_trading_pair(session, pair_canonical_id(CHAIN_ID, POOL))
    assert tp is not None
    assert tp.base_token_id == token_canonical_id(CHAIN_ID, TOKEN0)
    assert tp.quote_token_id == token_canonical_id(CHAIN_ID, TOKEN1)
    assert tp.dex == "uniswap_v2"
    assert tp.creation_block == 13_500_004


async def test_swap_executed_creates_wallets(
    pg_engine: AsyncEngine, clean_entities: _CleanFn
) -> None:
    await clean_entities()
    fact = _make_swap_fact()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_resolution.resolve_from_swap_executed(session, fact)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        w_sender = await repos.get_wallet(session, wallet_canonical_id(CHAIN_ID, SENDER))
        w_recipient = await repos.get_wallet(session, wallet_canonical_id(CHAIN_ID, RECIPIENT))
    assert w_sender is not None
    assert w_sender.first_seen_at == fact.event_time
    assert w_recipient is not None
    assert w_recipient.first_seen_at == fact.event_time


async def test_entity_resolution_idempotent(
    pg_engine: AsyncEngine, clean_entities: _CleanFn
) -> None:
    await clean_entities()
    fact = _make_pair_created_fact()

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_resolution.resolve_from_pair_created(session, fact)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_resolution.resolve_from_pair_created(session, fact)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        tp = await repos.get_trading_pair(session, pair_canonical_id(CHAIN_ID, POOL))
    assert tp is not None


async def test_wallet_first_seen_at_not_overwritten_by_later_fact(
    pg_engine: AsyncEngine, clean_entities: _CleanFn
) -> None:
    await clean_entities()
    early = datetime(2024, 4, 22, 12, 0, 0, tzinfo=UTC)
    late = datetime(2024, 4, 22, 13, 0, 0, tzinfo=UTC)

    fact_early = _make_swap_fact().model_copy(update={"event_time": early})
    fact_late = _make_swap_fact().model_copy(
        update={
            "event_time": late,
            "fact_id": f"{CHAIN_ID}:0x{'dd' * 32}:2",
            "tx_hash": f"0x{'dd' * 32}",
            "log_index": 2,
        }
    )

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_resolution.resolve_from_swap_executed(session, fact_early)
        await entity_resolution.resolve_from_swap_executed(session, fact_late)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        w = await repos.get_wallet(session, wallet_canonical_id(CHAIN_ID, SENDER))
    assert w is not None
    assert w.first_seen_at == early


async def test_list_pairs_for_token(pg_engine: AsyncEngine, clean_entities: _CleanFn) -> None:
    await clean_entities()
    fact1 = _make_pair_created_fact()
    pool2 = "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB"
    fact2 = BlockchainFact(
        schema_version="1.0",
        fact_id=f"{CHAIN_ID}:0x{'ee' * 32}:0",
        chain_id=CHAIN_ID,
        fact_type=FactType.PAIR_CREATED,
        block_number=13_500_010,
        block_hash=f"0x{'ff' * 32}",
        tx_hash=f"0x{'ee' * 32}",
        log_index=0,
        event_time=datetime(2024, 4, 22, 12, 1, 0, tzinfo=UTC),
        observed_at=PINNED_TIME,
        ingested_at=PINNED_TIME,
        confirmation_status=ConfirmationStatus.PENDING,
        confirmations=0,
        payload=PairCreatedPayload(
            fact_type="PAIR_CREATED",
            pair_address=pool2,
            token0_address=TOKEN0,
            token1_address="0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC",
            dex="uniswap_v2",
        ),
    )

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        await entity_resolution.resolve_from_pair_created(session, fact1)
        await entity_resolution.resolve_from_pair_created(session, fact2)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session:
        pairs = await repos.list_pairs_for_token(session, token_canonical_id(CHAIN_ID, TOKEN0))
    assert len(pairs) == 2
    assert all(tp.base_token_id == token_canonical_id(CHAIN_ID, TOKEN0) for tp in pairs)
