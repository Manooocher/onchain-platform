"""Part A ORM models (DOC-012 Part A, DOC-014 § Storage Assignment).

Slowly-changing registry objects stored in PostgreSQL. Column types follow
DOC-014 § Type Mapping Rules exactly. These are the ONLY files in the repo
allowed to know what a SQLAlchemy model looks like (DOC-011 § persistence/).

FK constraints where the reference is monomorphic (DOC-014 § Data Integrity
Constraints): TradingPair.base_token_id → Token.canonical_id,
TradingPair.quote_token_id → Token.canonical_id,
LiquidityPool.canonical_id → TradingPair.canonical_id.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from onchain_platform.domain.enums import ContractType, VerificationStatus


class EntityBase(DeclarativeBase):
    """Declarative base for Part A entity ORM models."""


class TokenRow(EntityBase):
    """Token entity (DOC-012 Part A)."""

    __tablename__ = "tokens"

    canonical_id: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contract_address: Mapped[str] = mapped_column(String(42), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False, server_default="UNKNOWN")
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default="Unknown Token")
    decimals: Mapped[int] = mapped_column(Integer, nullable=False, server_default="18")
    # Token Amount: NUMERIC(78,0) for uint256 (DOC-014 § Type Mapping Rules).
    total_supply: Mapped[str] = mapped_column(Numeric(78, 0), nullable=False, server_default="0")
    deployment_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        CheckConstraint("decimals >= 0 AND decimals <= 255", name="ck_token_decimals_range"),
        CheckConstraint("total_supply >= 0", name="ck_token_total_supply_non_negative"),
        Index("ix_tokens_chain_id", "chain_id"),
    )


class TradingPairRow(EntityBase):
    """TradingPair entity (DOC-012 Part A)."""

    __tablename__ = "trading_pairs"

    canonical_id: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dex: Mapped[str] = mapped_column(Text, nullable=False)
    # FK to tokens.canonical_id (monomorphic, DOC-014 § Data Integrity).
    base_token_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tokens.canonical_id"), nullable=False
    )
    quote_token_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tokens.canonical_id"), nullable=False
    )
    pool_address: Mapped[str] = mapped_column(String(42), nullable=False, unique=True)
    creation_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creation_fact_id: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        # DOC-014 § Indexing Strategy: "Which pairs exist for this token" —
        # two separate indexes, one per direction.
        Index("ix_trading_pairs_base_token", "base_token_id"),
        Index("ix_trading_pairs_quote_token", "quote_token_id"),
        Index("ix_trading_pairs_chain_id", "chain_id"),
    )


class LiquidityPoolRow(EntityBase):
    """LiquidityPool entity (DOC-012 Part A).

    canonical_id is the same as TradingPair.canonical_id — a Liquidity Pool
    does not have an identity independent of its pair in the MVP.
    """

    __tablename__ = "liquidity_pools"

    canonical_id: Mapped[str] = mapped_column(
        Text, ForeignKey("trading_pairs.canonical_id"), primary_key=True
    )
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    protocol: Mapped[str] = mapped_column(Text, nullable=False)
    fee_tier_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "fee_tier_bps IS NULL OR fee_tier_bps BETWEEN 0 AND 10000",
            name="ck_fee_tier_bps_range",
        ),
    )


class WalletRow(EntityBase):
    """Wallet entity (DOC-012 Part A)."""

    __tablename__ = "wallets"

    canonical_id: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    address: Mapped[str] = mapped_column(String(42), nullable=False, unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="ARRAY[]::TEXT[]"
    )

    __table_args__ = (Index("ix_wallets_chain_id", "chain_id"),)


class SmartContractRow(EntityBase):
    """SmartContract entity (DOC-012 Part A)."""

    __tablename__ = "smart_contracts"

    canonical_id: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    address: Mapped[str] = mapped_column(String(42), nullable=False)
    contract_type: Mapped[ContractType] = mapped_column(
        Enum(ContractType, name="contract_type_enum", native_enum=True), nullable=False
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    deployment_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        Index("ix_smart_contracts_address", "address"),
        Index("ix_smart_contracts_chain_id", "chain_id"),
    )


class MetadataRow(EntityBase):
    """Metadata entity (DOC-012 Part A).

    DOC-006: "Metadata never modifies a Blockchain Fact. This schema has no
    event_time — metadata is not a historical occurrence."
    """

    __tablename__ = "metadata"

    entity_id: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    social_links: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, server_default="{}")
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="verification_status_enum", native_enum=True),
        nullable=False,
        server_default="UNVERIFIED",
    )
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
