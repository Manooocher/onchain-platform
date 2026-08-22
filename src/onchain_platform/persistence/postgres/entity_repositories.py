"""Entity CRUD repositories (DOC-011 § persistence/, DOC-014 § Storage
Assignment).

Translation boundary: accepts and returns domain/ types only, never leaks
ORM instances upward (DOC-010 § Persistence Access Layer). All
SQLAlchemyError → PersistenceError (DOC-013 § Exception Hierarchy).

Upserts use ON CONFLICT on canonical_id (the natural key) — idempotent
replay guaranteed (ADR-006 § Idempotency).
"""

from typing import Any, cast

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.entities.liquidity_pool import LiquidityPool
from onchain_platform.domain.entities.metadata import Metadata
from onchain_platform.domain.entities.smart_contract import SmartContract
from onchain_platform.domain.entities.token import Token
from onchain_platform.domain.entities.trading_pair import TradingPair
from onchain_platform.domain.entities.wallet import Wallet
from onchain_platform.domain.exceptions import PersistenceError
from onchain_platform.domain.schemas.enums import ConfirmationStatus
from onchain_platform.persistence.postgres.facts import BlockchainFactRow
from onchain_platform.persistence.postgres.models import (
    LiquidityPoolRow,
    MetadataRow,
    SmartContractRow,
    TokenRow,
    TradingPairRow,
    WalletRow,
)

# --- Token ---


async def save_token(session: AsyncSession, token: Token) -> bool:
    """Upsert a Token entity. Returns True if newly inserted."""
    stmt = (
        pg_insert(TokenRow)
        .values(
            canonical_id=token.canonical_id,
            schema_version=token.schema_version,
            chain_id=token.chain_id,
            contract_address=token.contract_address,
            symbol=token.symbol,
            name=token.name,
            decimals=token.decimals,
            total_supply=token.total_supply,
            deployment_block=token.deployment_block,
        )
        .on_conflict_do_nothing(index_elements=["canonical_id"])
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save Token {token.canonical_id}") from exc
    return bool(result.rowcount == 1)


async def get_token(session: AsyncSession, canonical_id: str) -> Token | None:
    stmt = select(TokenRow).where(TokenRow.canonical_id == canonical_id)
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to read Token {canonical_id}") from exc
    if row is None:
        return None
    return Token(
        canonical_id=row.canonical_id,
        chain_id=row.chain_id,
        contract_address=row.contract_address,
        symbol=row.symbol,
        name=row.name,
        decimals=row.decimals,
        total_supply=str(row.total_supply),
        deployment_block=row.deployment_block,
    )


# --- TradingPair ---


async def save_trading_pair(session: AsyncSession, pair: TradingPair) -> bool:
    stmt = (
        pg_insert(TradingPairRow)
        .values(
            canonical_id=pair.canonical_id,
            schema_version=pair.schema_version,
            chain_id=pair.chain_id,
            dex=pair.dex,
            base_token_id=pair.base_token_id,
            quote_token_id=pair.quote_token_id,
            pool_address=pair.pool_address,
            creation_block=pair.creation_block,
            creation_fact_id=pair.creation_fact_id,
        )
        .on_conflict_do_nothing(index_elements=["canonical_id"])
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save TradingPair {pair.canonical_id}") from exc
    return bool(result.rowcount == 1)


async def get_trading_pair(session: AsyncSession, canonical_id: str) -> TradingPair | None:
    stmt = select(TradingPairRow).where(TradingPairRow.canonical_id == canonical_id)
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to read TradingPair {canonical_id}") from exc
    if row is None:
        return None
    return TradingPair(
        canonical_id=row.canonical_id,
        chain_id=row.chain_id,
        dex=row.dex,
        base_token_id=row.base_token_id,
        quote_token_id=row.quote_token_id,
        pool_address=row.pool_address,
        creation_block=row.creation_block,
        creation_fact_id=row.creation_fact_id,
    )


async def list_pairs_for_token(session: AsyncSession, token_canonical_id: str) -> list[TradingPair]:
    """All pairs where token is base or quote (DOC-014 § Indexing Strategy:
    two separate indexes, one per direction)."""
    stmt = (
        select(TradingPairRow)
        .where(
            (TradingPairRow.base_token_id == token_canonical_id)
            | (TradingPairRow.quote_token_id == token_canonical_id)
        )
        .order_by(TradingPairRow.creation_block)
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list pairs for token {token_canonical_id}") from exc
    return [
        TradingPair(
            canonical_id=r.canonical_id,
            chain_id=r.chain_id,
            dex=r.dex,
            base_token_id=r.base_token_id,
            quote_token_id=r.quote_token_id,
            pool_address=r.pool_address,
            creation_block=r.creation_block,
            creation_fact_id=r.creation_fact_id,
        )
        for r in rows
    ]


async def list_all_trading_pairs(session: AsyncSession) -> list[TradingPair]:
    """All trading pairs whose creating PAIR_CREATED fact is FINALIZED.

    "Finality Before Analytics" (ADR-006): an outcome must never be
    evaluated for a pair whose creation fact is still PENDING/CONFIRMED or
    has been ORPHANED — the pair itself is only valid once its creation is
    finalized. Only pairs whose creation_fact_id references a FINALIZED
    blockchain_fact row are returned.

    ORDERED by creation_fact_id (block,log-order via event_time) for
    determinism (DOC-013 § Determinism Discipline).
    """
    stmt = (
        select(TradingPairRow)
        .join(BlockchainFactRow, TradingPairRow.creation_fact_id == BlockchainFactRow.fact_id)
        .where(BlockchainFactRow.confirmation_status == ConfirmationStatus.FINALIZED)
        .order_by(BlockchainFactRow.event_time, TradingPairRow.canonical_id)
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError("failed to list all trading pairs") from exc
    return [
        TradingPair(
            canonical_id=r.canonical_id,
            chain_id=r.chain_id,
            dex=r.dex,
            base_token_id=r.base_token_id,
            quote_token_id=r.quote_token_id,
            pool_address=r.pool_address,
            creation_block=r.creation_block,
            creation_fact_id=r.creation_fact_id,
        )
        for r in rows
    ]


# --- LiquidityPool ---


async def save_liquidity_pool(session: AsyncSession, pool: LiquidityPool) -> bool:
    stmt = (
        pg_insert(LiquidityPoolRow)
        .values(
            canonical_id=pool.canonical_id,
            schema_version=pool.schema_version,
            protocol=pool.protocol,
            fee_tier_bps=pool.fee_tier_bps,
        )
        .on_conflict_do_nothing(index_elements=["canonical_id"])
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save LiquidityPool {pool.canonical_id}") from exc
    return bool(result.rowcount == 1)


# --- Wallet ---


async def save_wallet(session: AsyncSession, wallet: Wallet) -> bool:
    """Upsert a Wallet entity. first_seen_at is updated only if the new
    value is earlier (idempotent replay — ADR-006 § Idempotency)."""
    stmt = (
        pg_insert(WalletRow)
        .values(
            canonical_id=wallet.canonical_id,
            schema_version=wallet.schema_version,
            chain_id=wallet.chain_id,
            address=wallet.address,
            first_seen_at=wallet.first_seen_at,
            tags=wallet.tags,
        )
        .on_conflict_do_update(
            index_elements=["canonical_id"],
            set_={
                # Only update first_seen_at if the new value is earlier.
                "first_seen_at": func.least(
                    WalletRow.first_seen_at, text("EXCLUDED.first_seen_at")
                ),
            },
        )
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save Wallet {wallet.canonical_id}") from exc
    return bool(result.rowcount == 1)


async def get_wallet(session: AsyncSession, canonical_id: str) -> Wallet | None:
    stmt = select(WalletRow).where(WalletRow.canonical_id == canonical_id)
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to read Wallet {canonical_id}") from exc
    if row is None:
        return None
    return Wallet(
        canonical_id=row.canonical_id,
        chain_id=row.chain_id,
        address=row.address,
        first_seen_at=row.first_seen_at,
        tags=row.tags,
    )


# --- SmartContract ---


async def save_smart_contract(session: AsyncSession, contract: SmartContract) -> bool:
    stmt = (
        pg_insert(SmartContractRow)
        .values(
            canonical_id=contract.canonical_id,
            schema_version=contract.schema_version,
            chain_id=contract.chain_id,
            address=contract.address,
            contract_type=contract.contract_type,
            is_verified=contract.is_verified,
            deployment_block=contract.deployment_block,
        )
        .on_conflict_do_nothing(index_elements=["canonical_id"])
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save SmartContract {contract.canonical_id}") from exc
    return bool(result.rowcount == 1)


# --- Metadata ---


async def save_metadata(session: AsyncSession, metadata: Metadata) -> bool:
    stmt = (
        pg_insert(MetadataRow)
        .values(
            entity_id=metadata.entity_id,
            schema_version=metadata.schema_version,
            website=metadata.website,
            social_links=metadata.social_links,
            logo_url=metadata.logo_url,
            description=metadata.description,
            verification_status=metadata.verification_status,
            last_updated=metadata.last_updated,
        )
        .on_conflict_do_update(
            index_elements=["entity_id"],
            set_={
                "website": metadata.website,
                "social_links": metadata.social_links,
                "logo_url": metadata.logo_url,
                "description": metadata.description,
                "verification_status": metadata.verification_status,
                "last_updated": metadata.last_updated,
            },
        )
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to save Metadata {metadata.entity_id}") from exc
    return bool(result.rowcount == 1)
