"""Fix checkpoints.chain_id: remove the auto-increment sequence default and
add a positive CHECK constraint (Technical Debt TD-4).

`chain_id` is an EIP-155 id (a foreign key to blockchains.chain_id) — it must
always be explicitly provided, never auto-generated. The M2 migration created
the column without a default, but a leftover `checkpoints_chain_id_seq`
`nextval` default had appeared in the live schema; this migration removes it
and hardens the column with a CHECK (chain_id > 0).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e8f0c1b2a3"
down_revision: str | None = "c8e7e2d9a2b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove the auto-increment default on chain_id. The sequence may or may
    # not exist (checkfirst semantics via IF EXISTS not available on
    # alter_column), so clear the server_default unconditionally; dropping the
    # lingering sequence is idempotent-safe via DO block.
    op.alter_column(
        "checkpoints",
        "chain_id",
        server_default=None,
    )
    # Drop the lingering sequence object if present (harmless if absent).
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_class WHERE relname='checkpoints_chain_id_seq') THEN
            EXECUTE 'DROP SEQUENCE checkpoints_chain_id_seq';
          END IF;
        END $$;
        """
    )
    # chain_id is an EIP-155 id — must be a positive integer.
    op.create_check_constraint(
        "check_chain_id_positive",
        "checkpoints",
        "chain_id > 0",
    )


def downgrade() -> None:
    op.drop_constraint("check_chain_id_positive", "checkpoints")
    op.alter_column(
        "checkpoints",
        "chain_id",
        server_default=sa.text("nextval('checkpoints_chain_id_seq')"),
    )