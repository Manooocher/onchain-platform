"""Persistence translation boundary (DOC-011 § persistence/).

Translates domain Canonical Schemas ↔ SQLAlchemy ORM rows, for the models in
facts.py. This file accepts and returns domain/ types only — it never leaks
an ORM instance upward (DOC-010 § Persistence Access Layer: "Business Logic
must never see ORM models"; DOC-011: repositories.py is the translation
boundary).

Every infrastructure exception is translated to a PlatformError subclass
before it crosses this boundary (DOC-013 § Exception Hierarchy): a raw
SQLAlchemyError must never propagate out of persistence/.
"""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.exceptions import PersistenceError
from onchain_platform.domain.schemas.blockchain_fact import BlockchainFact
from onchain_platform.domain.schemas.checkpoint import Checkpoint
from onchain_platform.domain.schemas.enums import ConfirmationStatus
from onchain_platform.persistence.postgres.facts import BlockchainFactRow, CheckpointRow


def _fact_to_row_values(fact: BlockchainFact) -> dict[str, object]:
    """Domain schema → column values. payload goes in as JSONB (DOC-014 §
    The Discriminated Payload); Pydantic has already validated its shape."""
    return {
        "fact_id": fact.fact_id,
        "schema_version": fact.schema_version,
        "chain_id": fact.chain_id,
        "fact_type": fact.fact_type,
        "block_number": fact.block_number,
        "block_hash": fact.block_hash,
        "tx_hash": fact.tx_hash,
        "log_index": fact.log_index,
        "event_time": fact.event_time,
        "observed_at": fact.observed_at,
        "ingested_at": fact.ingested_at,
        "confirmation_status": fact.confirmation_status,
        "confirmations": fact.confirmations,
        "payload": fact.payload.model_dump(),
    }


def _row_to_fact(row: BlockchainFactRow) -> BlockchainFact:
    """ORM row → domain schema. Re-validates through Pydantic on read —
    schema_version plus the discriminated union are what let
    processing/schema_dispatcher.py handle schema evolution (DOC-012 §
    Schema Versioning Policy)."""
    payload = dict(row.payload)
    return BlockchainFact(
        schema_version=row.schema_version,
        fact_id=row.fact_id,
        chain_id=row.chain_id,
        fact_type=row.fact_type,
        block_number=row.block_number,
        block_hash=row.block_hash,
        tx_hash=row.tx_hash,
        log_index=row.log_index,
        event_time=_ensure_utc(row.event_time),
        observed_at=_ensure_utc(row.observed_at),
        ingested_at=_ensure_utc(row.ingested_at),
        confirmation_status=row.confirmation_status,
        confirmations=row.confirmations,
        payload=payload,  # type: ignore[arg-type]  # discriminated union input
    )


def _ensure_utc(value: datetime) -> datetime:
    # TIMESTAMPTZ always returns tz-aware values; normalize to UTC so
    # comparisons and serialization are canonical (DOC-014: never bare
    # TIMESTAMP).
    if value.tzinfo is None:  # defensive only — TIMESTAMPTZ guarantees aware
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def save_fact(session: AsyncSession, fact: BlockchainFact) -> bool:
    """Insert a BlockchainFact idempotently. Returns True if a new row was
    inserted, False if a row with this fact_id already existed.

    Idempotent by construction: INSERT ... ON CONFLICT (fact_id) DO NOTHING
    (ADR-006 § Persistence Rules — duplicates are expected behavior from RPC
    retries, restarts, and replays; they must never create duplicate facts).

    Milestone 1 is insert-only: there is no UPDATE path here at all. The
    row-level immutability guard for FINALIZED rows (DOC-013 § Immutability
    & State Modeling) arrives with the first UPDATE path in Milestone 2 —
    and will raise PersistenceError on violation, never a silent no-op.
    """
    stmt = (
        pg_insert(BlockchainFactRow)
        .values(**_fact_to_row_values(fact))
        .on_conflict_do_nothing(index_elements=["fact_id"])
    )
    try:
        # CursorResult carries rowcount for DML (INSERT) — execute()'s
        # declared return type is the wider Result, hence the cast.
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to insert BlockchainFact {fact.fact_id}") from exc
    return bool(result.rowcount == 1)


async def get_fact(session: AsyncSession, fact_id: str) -> BlockchainFact | None:
    """Read one BlockchainFact by its natural key."""
    stmt = select(BlockchainFactRow).where(BlockchainFactRow.fact_id == fact_id)
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to read BlockchainFact {fact_id}") from exc
    return _row_to_fact(row) if row is not None else None


async def list_facts_for_chain(session: AsyncSession, chain_id: int) -> list[BlockchainFact]:
    """All facts for a chain, in deterministic order: block_number, then
    log_index (DOC-013 § Determinism Discipline — ordered iteration only on
    any path that produces an aggregate or a comparison)."""
    stmt = (
        select(BlockchainFactRow)
        .where(BlockchainFactRow.chain_id == chain_id)
        .order_by(BlockchainFactRow.block_number, BlockchainFactRow.log_index)
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to list facts for chain {chain_id}") from exc
    return [_row_to_fact(row) for row in rows]


async def count_facts_for_chain(session: AsyncSession, chain_id: int) -> int:
    """Row count for a chain — used to prove idempotency (ADR-006 §
    Idempotency): the same block range processed twice must not change it."""
    stmt = (
        select(func.count())
        .select_from(BlockchainFactRow)
        .where(BlockchainFactRow.chain_id == chain_id)
    )
    try:
        return int((await session.execute(stmt)).scalar_one())
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to count facts for chain {chain_id}") from exc


# ---------------------------------------------------------------------------
# Confirmation Lifecycle (Milestone 2 — ADR-006 § Confirmation Lifecycle)
# ---------------------------------------------------------------------------


async def advance_confirmation_counts(
    session: AsyncSession,
    chain_id: int,
    current_chain_head: int,
    confirmation_depth: int,
) -> None:
    """Advance confirmations for all non-terminal facts on a chain.

    Called once per new block by the Finality Engine. For each PENDING or
    CONFIRMED fact: confirmations = current_chain_head - fact.block_number.
    Facts whose confirmations >= confirmation_depth transition to FINALIZED.

    DOC-013 § Immutability & State Modeling: the row-level guard is evaluated
    here — FINALIZED rows are never touched by this UPDATE (the WHERE clause
    excludes them).
    """
    # confirmations = head - block_number for all non-terminal facts.
    # Status transitions: PENDING → CONFIRMED (confirmations >= 1),
    #                     CONFIRMED → FINALIZED (confirmations >= depth).
    # The CASE expression handles both transitions in one pass.
    depth = confirmation_depth
    stmt = (
        update(BlockchainFactRow)
        .where(
            BlockchainFactRow.chain_id == chain_id,
            BlockchainFactRow.confirmation_status.in_(
                [
                    ConfirmationStatus.PENDING,
                    ConfirmationStatus.CONFIRMED,
                ]
            ),
        )
        .values(
            confirmations=current_chain_head - BlockchainFactRow.block_number,
            confirmation_status=case(
                (
                    current_chain_head - BlockchainFactRow.block_number >= depth,
                    ConfirmationStatus.FINALIZED,
                ),
                (
                    current_chain_head - BlockchainFactRow.block_number >= 1,
                    ConfirmationStatus.CONFIRMED,
                ),
                else_=BlockchainFactRow.confirmation_status,
            ),
        )
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to advance confirmations for chain {chain_id}") from exc


async def mark_facts_orphaned(
    session: AsyncSession,
    chain_id: int,
    from_block: int,
    to_block: int,
) -> int:
    """Mark all non-finalized facts in [from_block, to_block] as ORPHANED.

    ADR-006 § Orphaned: "The Fact remains stored for auditability but is
    excluded from downstream processing. Facts are never deleted. Only their
    confirmation status changes."

    DOC-013 § Immutability: FINALIZED rows before the fork point are NEVER
    touched — the WHERE clause excludes them. Only PENDING/CONFIRMED facts
    in the orphaned range are marked ORPHANED.

    Returns the number of rows affected.
    """
    stmt = (
        update(BlockchainFactRow)
        .where(
            BlockchainFactRow.chain_id == chain_id,
            BlockchainFactRow.block_number >= from_block,
            BlockchainFactRow.block_number <= to_block,
            BlockchainFactRow.confirmation_status.in_(
                [
                    ConfirmationStatus.PENDING,
                    ConfirmationStatus.CONFIRMED,
                ]
            ),
        )
        .values(
            confirmation_status=ConfirmationStatus.ORPHANED,
            confirmations=0,
        )
    )
    try:
        result = cast("CursorResult[Any]", await session.execute(stmt))
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(
            f"failed to mark facts orphaned for chain {chain_id} blocks {from_block}..{to_block}"
        ) from exc
    return int(result.rowcount)


async def get_checkpoint(session: AsyncSession, chain_id: int) -> Checkpoint | None:
    """Read the checkpoint for a chain (DOC-012 § B.0). Returns None if no
    checkpoint exists yet (first run)."""
    stmt = select(CheckpointRow).where(CheckpointRow.chain_id == chain_id)
    try:
        row = (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to read checkpoint for chain {chain_id}") from exc
    if row is None:
        return None
    return Checkpoint(
        chain_id=row.chain_id,
        last_finalized_block=row.last_finalized_block,
        last_finalized_at=_ensure_utc(row.last_finalized_at),
        updated_at=_ensure_utc(row.updated_at),
    )


async def save_checkpoint(session: AsyncSession, checkpoint: Checkpoint) -> None:
    """Upsert a checkpoint (DOC-012 § B.0 — mutable singleton per chain).

    Checkpoint is the deliberate counterexample to BlockchainFact's
    immutability: it is supposed to be overwritten in place as ingestion
    advances (DOC-013 § Immutability & State Modeling).
    """
    stmt = (
        pg_insert(CheckpointRow)
        .values(
            chain_id=checkpoint.chain_id,
            last_finalized_block=checkpoint.last_finalized_block,
            last_finalized_at=checkpoint.last_finalized_at,
            updated_at=checkpoint.updated_at,
        )
        .on_conflict_do_update(
            index_elements=["chain_id"],
            set_={
                "last_finalized_block": checkpoint.last_finalized_block,
                "last_finalized_at": checkpoint.last_finalized_at,
                "updated_at": checkpoint.updated_at,
            },
        )
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except SQLAlchemyError as exc:
        raise PersistenceError(
            f"failed to save checkpoint for chain {checkpoint.chain_id}"
        ) from exc
