"""insights table (DOC-012 § B.4, DOC-014 § Storage Assignment).

Hand-written migration: insights table with importance ENUM, source_features
TEXT[], and (entity_id, generated_at) index per DOC-014 § Indexing Strategy.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    importance_enum = sa.Enum(
        "LOW", "MEDIUM", "HIGH",
        name="importance_enum",
        native_enum=True,
    )
    importance_enum.create(op.get_bind(), checkfirst=True)

    op.execute(
        """
        CREATE TABLE insights (
            insight_id      TEXT        PRIMARY KEY,
            schema_version  TEXT        NOT NULL DEFAULT '1.0',
            entity_id       TEXT        NOT NULL,
            insight_type    TEXT        NOT NULL,
            summary         TEXT        NOT NULL,
            generated_at    TIMESTAMPTZ NOT NULL,
            source_features TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
            importance      importance_enum NOT NULL
        )
        """
    )

    # DOC-014 § Indexing Strategy: "Research querying 'what happened
    # to this entity' without needing every historical outcome scanned."
    op.execute(
        "CREATE INDEX ix_insights_entity_time ON insights (entity_id, generated_at)"
    )


def downgrade() -> None:
    # Forward-only (DOC-014 § Migration Policy).
    pass
