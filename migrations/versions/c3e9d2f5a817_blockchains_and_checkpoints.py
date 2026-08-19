"""blockchains registry table (DOC-012 Part A) with seed rows, and the
checkpoints table (DOC-012 § B.0).

Seed values (ImplementationPlan § Open Decisions): avg_block_time_seconds is
seeded with each chain's current published average and treated as a config
value to correct later, not a constant. blockchains is conventional mutable
operational data — standard migrations apply (DOC-014 § Migration Policy).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e9d2f5a817"
down_revision: str | None = "b1c7f0a9d401"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blockchains",
        sa.Column("chain_id", sa.BigInteger, primary_key=True),
        sa.Column("schema_version", sa.Text, nullable=False, server_default="1.0"),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("native_asset_symbol", sa.Text, nullable=False),
        sa.Column("is_supported", sa.Boolean, nullable=False),
        # DOUBLE PRECISION — genuinely float (DOC-014 § Type Mapping Rules;
        # DOC-012 § Clarifying an ambiguity in DOC-008).
        sa.Column("avg_block_time_seconds", sa.Double, nullable=False),
    )
    op.execute(
        """
        INSERT INTO blockchains
            (chain_id, schema_version, name, native_asset_symbol, is_supported,
             avg_block_time_seconds)
        VALUES
            (1,    '1.0', 'Ethereum',  'ETH', TRUE, 12.0),
            (8453, '1.0', 'Base',      'ETH', TRUE, 2.0),
            (56,   '1.0', 'BNB Chain', 'BNB', TRUE, 0.75)
        """
    )

    # Checkpoint (DOC-012 § B.0): mutable singleton per chain.
    op.create_table(
        "checkpoints",
        sa.Column("chain_id", sa.BigInteger, primary_key=True),
        sa.Column("last_finalized_block", sa.BigInteger, nullable=False),
        sa.Column("last_finalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    # checkpoints/blockchains are mutable operational tables — dropping is
    # acceptable here (unlike blockchain_facts, DOC-014 § Migration Policy).
    op.drop_table("checkpoints")
    op.drop_table("blockchains")
