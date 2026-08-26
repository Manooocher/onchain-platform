"""Add liquidity-usd confidence tracking columns to observation_snapshots
(TD-1 Phase 3).

Adds provenance + reliability metadata for liquidity_usd so ML Foundation can
weight USD features by confidence:
- liquidity_usd_source      TEXT   (STATIC | CHAINLINK | DEX_RATIO | NULL)
- liquidity_usd_confidence  DOUBLE PRECISION (0.0..1.0)
- quote_token_type          TEXT   (USDC | WETH | STABLECOIN | OTHER)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f3a5b7d9c1"
down_revision: str | None = "d9e8f0c1b2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "observation_snapshots",
        sa.Column("liquidity_usd_source", sa.Text(), nullable=True),
    )
    op.add_column(
        "observation_snapshots",
        sa.Column("liquidity_usd_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "observation_snapshots",
        sa.Column("quote_token_type", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # Forward-only (DOC-014 § Migration Policy).
    pass
