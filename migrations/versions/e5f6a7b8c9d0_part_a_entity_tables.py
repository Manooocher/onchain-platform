"""Part A entity tables (DOC-012 Part A, DOC-014 § Storage Assignment).

Hand-written migration: tokens, trading_pairs, liquidity_pools, wallets,
smart_contracts, metadata tables with FK constraints and indexes per
DOC-014 § Indexing Strategy and § Data Integrity Constraints.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4f8a1b2c3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Native Postgres ENUMs (DOC-014 § Standard mappings).
    contract_type_enum = sa.Enum(
        "ERC20", "FACTORY", "ROUTER", "POOL", "UNKNOWN",
        name="contract_type_enum", native_enum=True,
    )
    contract_type_enum.create(op.get_bind(), checkfirst=True)

    verification_status_enum = sa.Enum(
        "UNVERIFIED", "PENDING", "VERIFIED",
        name="verification_status_enum", native_enum=True,
    )
    verification_status_enum.create(op.get_bind(), checkfirst=True)

    # --- tokens ---
    op.execute("""
        CREATE TABLE tokens (
            canonical_id        TEXT        PRIMARY KEY,
            schema_version      TEXT        NOT NULL DEFAULT '1.0',
            chain_id            BIGINT      NOT NULL,
            contract_address    VARCHAR(42) NOT NULL UNIQUE,
            symbol              TEXT        NOT NULL DEFAULT 'UNKNOWN',
            name                TEXT        NOT NULL DEFAULT 'Unknown Token',
            decimals            INTEGER     NOT NULL DEFAULT 18,
            total_supply        NUMERIC(78,0) NOT NULL DEFAULT 0,
            deployment_block    BIGINT,
            CONSTRAINT ck_token_decimals_range CHECK (decimals >= 0 AND decimals <= 255),
            CONSTRAINT ck_token_total_supply_non_negative CHECK (total_supply >= 0)
        )
    """)
    op.execute("CREATE INDEX ix_tokens_chain_id ON tokens (chain_id)")

    # --- trading_pairs ---
    op.execute("""
        CREATE TABLE trading_pairs (
            canonical_id        TEXT        PRIMARY KEY,
            schema_version      TEXT        NOT NULL DEFAULT '1.0',
            chain_id            BIGINT      NOT NULL,
            dex                 TEXT        NOT NULL,
            base_token_id       TEXT        NOT NULL REFERENCES tokens(canonical_id),
            quote_token_id      TEXT        NOT NULL REFERENCES tokens(canonical_id),
            pool_address        VARCHAR(42) NOT NULL UNIQUE,
            creation_block      BIGINT      NOT NULL,
            creation_fact_id    TEXT        NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_trading_pairs_base_token ON trading_pairs (base_token_id)")
    op.execute("CREATE INDEX ix_trading_pairs_quote_token ON trading_pairs (quote_token_id)")
    op.execute("CREATE INDEX ix_trading_pairs_chain_id ON trading_pairs (chain_id)")

    # --- liquidity_pools ---
    op.execute("""
        CREATE TABLE liquidity_pools (
            canonical_id        TEXT        PRIMARY KEY REFERENCES trading_pairs(canonical_id),
            schema_version      TEXT        NOT NULL DEFAULT '1.0',
            protocol            TEXT        NOT NULL,
            fee_tier_bps        INTEGER,
            CONSTRAINT ck_fee_tier_bps_range CHECK (fee_tier_bps IS NULL OR fee_tier_bps BETWEEN 0 AND 10000)
        )
    """)

    # --- wallets ---
    op.execute("""
        CREATE TABLE wallets (
            canonical_id        TEXT        PRIMARY KEY,
            schema_version      TEXT        NOT NULL DEFAULT '1.0',
            chain_id            BIGINT      NOT NULL,
            address             VARCHAR(42) NOT NULL UNIQUE,
            first_seen_at       TIMESTAMPTZ NOT NULL,
            tags                TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[]
        )
    """)
    op.execute("CREATE INDEX ix_wallets_chain_id ON wallets (chain_id)")

    # --- smart_contracts ---
    op.execute("""
        CREATE TABLE smart_contracts (
            canonical_id        TEXT        PRIMARY KEY,
            schema_version      TEXT        NOT NULL DEFAULT '1.0',
            chain_id            BIGINT      NOT NULL,
            address             VARCHAR(42) NOT NULL,
            contract_type       contract_type_enum NOT NULL,
            is_verified         BOOLEAN     NOT NULL DEFAULT FALSE,
            deployment_block    BIGINT
        )
    """)
    op.execute("CREATE INDEX ix_smart_contracts_address ON smart_contracts (address)")
    op.execute("CREATE INDEX ix_smart_contracts_chain_id ON smart_contracts (chain_id)")

    # --- metadata ---
    op.execute("""
        CREATE TABLE metadata (
            entity_id           TEXT        PRIMARY KEY,
            schema_version      TEXT        NOT NULL DEFAULT '1.0',
            website             TEXT,
            social_links        JSONB       NOT NULL DEFAULT '{}',
            logo_url            TEXT,
            description         TEXT,
            verification_status verification_status_enum NOT NULL DEFAULT 'UNVERIFIED',
            last_updated        TIMESTAMPTZ NOT NULL
        )
    """)


def downgrade() -> None:
    # Forward-only (DOC-014 § Migration Policy).
    pass
