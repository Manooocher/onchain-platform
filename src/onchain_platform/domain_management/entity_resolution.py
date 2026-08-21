"""Entity Resolution service (DOC-011 § domain_management/, DOC-006 §
Ownership table).

Eager, synchronous entity resolution on fact ingestion (ImplementationPlan
§ Milestone 4: "entity resolution should be synchronous within the fact
processor, not a separate async pipeline — simplicity over sophistication,
DOC-004").

All upserts are idempotent (ON CONFLICT on canonical_id) — replay
produces identical entity sets (ADR-006 § Idempotency). Canonical IDs
are stable across reorgs (addresses are immutable on-chain).
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.entities.liquidity_pool import LiquidityPool
from onchain_platform.domain.entities.smart_contract import SmartContract
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.entities.wallet import Wallet
from onchain_platform.domain.enums import ContractType
from onchain_platform.domain.ids import (
    pair_canonical_id,
    smart_contract_canonical_id,
    token_canonical_id,
    wallet_canonical_id,
)
from onchain_platform.domain.schemas.blockchain_fact import (
    BlockchainFact,
    PairCreatedPayload,
    SwapExecutedPayload,
)
from onchain_platform.persistence.postgres import entity_repositories as repos
from onchain_platform.persistence.postgres.entity_repositories import save_wallet

logger = structlog.get_logger(__name__)


async def resolve_from_pair_created(session: AsyncSession, fact: BlockchainFact) -> None:
    assert isinstance(fact.payload, PairCreatedPayload)
    payload = fact.payload
    chain_id = fact.chain_id

    # SmartContract for token0 (ERC20).
    token0_sc = SmartContract(
        canonical_id=smart_contract_canonical_id(chain_id, payload.token0_address),
        chain_id=chain_id,
        address=payload.token0_address,
        contract_type=ContractType.ERC20,
    )
    await repos.save_smart_contract(session, token0_sc)

    # SmartContract for token1 (ERC20).
    token1_sc = SmartContract(
        canonical_id=smart_contract_canonical_id(chain_id, payload.token1_address),
        chain_id=chain_id,
        address=payload.token1_address,
        contract_type=ContractType.ERC20,
    )
    await repos.save_smart_contract(session, token1_sc)

    # SmartContract for pair (POOL).
    pair_sc = SmartContract(
        canonical_id=smart_contract_canonical_id(chain_id, payload.pair_address),
        chain_id=chain_id,
        address=payload.pair_address,
        contract_type=ContractType.POOL,
    )
    await repos.save_smart_contract(session, pair_sc)

    # Token for token0 (stub metadata — symbol/name/decimals are
    # placeholders until real metadata enrichment arrives).
    token0 = Token(
        canonical_id=token_canonical_id(chain_id, payload.token0_address),
        chain_id=chain_id,
        contract_address=payload.token0_address,
    )
    await repos.save_token(session, token0)

    # Token for token1.
    token1 = Token(
        canonical_id=token_canonical_id(chain_id, payload.token1_address),
        chain_id=chain_id,
        contract_address=payload.token1_address,
    )
    await repos.save_token(session, token1)

    # TradingPair.
    pair_cid = pair_canonical_id(chain_id, payload.pair_address)
    tp = TradingPair(
        canonical_id=pair_cid,
        chain_id=chain_id,
        dex=payload.dex,
        base_token_id=token_canonical_id(chain_id, payload.token0_address),
        quote_token_id=token_canonical_id(chain_id, payload.token1_address),
        pool_address=payload.pair_address,
        creation_block=fact.block_number,
        creation_fact_id=fact.fact_id,
    )
    await repos.save_trading_pair(session, tp)

    # LiquidityPool (same canonical_id as TradingPair — DOC-012 Part A:
    # "a Liquidity Pool does not have an identity independent of its
    # pair in the MVP").
    lp = LiquidityPool(
        canonical_id=pair_cid,
        protocol=payload.dex,
    )
    await repos.save_liquidity_pool(session, lp)

    logger.info(
        "entities_resolved",
        chain_id=chain_id,
        fact_type="PAIR_CREATED",
        pair_address=payload.pair_address,
        token0=payload.token0_address,
        token1=payload.token1_address,
    )


async def resolve_from_swap_executed(session: AsyncSession, fact: BlockchainFact) -> None:
    """Resolve entities from a SWAP_EXECUTED fact.

    Creates: Wallet for sender and recipient (if not already present).
    first_seen_at is updated only if the new value is earlier (idempotent
    replay — ADR-006 § Idempotency).
    """
    assert isinstance(fact.payload, SwapExecutedPayload)
    payload = fact.payload
    chain_id = fact.chain_id

    for address in (payload.sender, payload.recipient):
        wallet = Wallet(
            canonical_id=wallet_canonical_id(chain_id, address),
            chain_id=chain_id,
            address=address,
            first_seen_at=fact.event_time,
        )
        await save_wallet(session, wallet)

    logger.debug(
        "wallets_resolved",
        chain_id=chain_id,
        fact_type="SWAP_EXECUTED",
        sender=payload.sender,
        recipient=payload.recipient,
    )
