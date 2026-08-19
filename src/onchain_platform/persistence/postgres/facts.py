"""SQLAlchemy ORM for blockchain_facts (DOC-012 § B.1) + checkpoints
(DOC-012 § B.0).

Two different mutability semantics in one file, intentionally (DOC-011 §
persistence/): both are Postgres-resident ingestion state that nothing
outside acquisition/processing should touch directly. blockchain_facts is
append-only once FINALIZED (DOC-013 § Immutability & State Modeling);
checkpoints is a mutable singleton per chain_id (DOC-012 § B.0).

Column types follow DOC-014 § Type Mapping Rules exactly. ORM models never
leak outside persistence/ (DOC-011 § What Does Not Belong Here; DOC-010 §
Persistence Access Layer: "Business Logic must never see ORM models").
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from onchain_platform.domain.schemas.enums import ConfirmationStatus, FactType


class Base(DeclarativeBase):
    """Declarative base for all Postgres ORM models."""


class BlockchainFactRow(Base):
    """One row per BlockchainFact (DOC-012 § B.1).

    PK is the natural composite key fact_id = "{chain_id}:{tx_hash}:
    {log_index}" — no surrogate UUID (ADR-006 § Idempotency). Append-only
    once confirmation_status = FINALIZED: the row-level immutability guard
    lives in repositories.py (DOC-013 § Immutability & State Modeling) and
    is enforced before any UPDATE statement. Milestone 1 has no UPDATE path
    at all — insert only.
    """

    __tablename__ = "blockchain_facts"

    # Canonical ID / str identifier → TEXT, never VARCHAR(n) with an
    # arbitrary limit (DOC-014 § Standard mappings).
    fact_id: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    # Block numbers are monotonically increasing forever — BIGINT, not
    # INTEGER (DOC-014 § Standard mappings).
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fact_type: Mapped[FactType] = mapped_column(
        Enum(FactType, name="fact_type_enum", native_enum=True), nullable=False
    )
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 0x + 64 hex, always — fixed width catches malformed hashes at insert
    # time (DOC-014 § Standard mappings).
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    log_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Triple Timestamp Standard — always TIMESTAMPTZ, never bare TIMESTAMP
    # (DOC-014 § Standard mappings).
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmation_status: Mapped[ConfirmationStatus] = mapped_column(
        Enum(ConfirmationStatus, name="confirmation_status_enum", native_enum=True),
        nullable=False,
    )
    confirmations: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Discriminated payload: JSONB, not a wide table (DOC-014 § The
    # Discriminated Payload). Pydantic validates the shape before this row is
    # ever written; schema_version tells processing/schema_dispatcher.py
    # which model to validate against on read.
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    # Wallet involvement: STORED generated column computed once at write
    # time (DOC-014 § One field earns that treatment already). PAIR_CREATED
    # has no wallet fields → empty array.
    involved_wallets: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("ARRAY[]::TEXT[]"),
    )

    __table_args__ = (
        CheckConstraint("confirmations >= 0", name="ck_confirmations_non_negative"),
        # Finality Engine re-checking PENDING/CONFIRMED facts as new blocks
        # arrive (ADR-006 § Canonical Chain Validation Engine) — without
        # this, that scan is a full table scan (DOC-014 § Indexing Strategy).
        Index("ix_blockchain_facts_chain_status", "chain_id", "confirmation_status"),
        # Reorg resolution walking a specific block range (ADR-006).
        Index("ix_blockchain_facts_chain_block", "chain_id", "block_number"),
        # GIN index on involved_wallets for /v1/wallets/{id}/activity
        # membership queries (DOC-014 § Indexing Strategy).
        Index(
            "ix_blockchain_facts_involved_wallets",
            "involved_wallets",
            postgresql_using="gin",
        ),
    )


class CheckpointRow(Base):
    """Ingestion checkpoint per chain (DOC-012 § B.0).

    Mutable, singleton per chain_id, overwritten in place as ingestion
    progresses — the direct opposite of blockchain_facts. Read by
    acquisition/checkpoint.py; written only by processing/finality_engine.py
    (DOC-011). Neither file exists yet — both are Milestone 2.
    """

    __tablename__ = "checkpoints"

    chain_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_finalized_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BlockchainRow(Base):
    """Blockchain registry row (DOC-012 Part A).

    avg_block_time_seconds is DOUBLE PRECISION — one of only two genuinely
    float fields in the schema set (DOC-014 § Type Mapping Rules,
    "Genuinely float"; DOC-012 § Clarifying an ambiguity in DOC-008).
    """

    __tablename__ = "blockchains"

    chain_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    native_asset_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    is_supported: Mapped[bool] = mapped_column(nullable=False)
    avg_block_time_seconds: Mapped[float] = mapped_column(nullable=False)
