"""outcomes table (DOC-012 § B.4, DOC-014 § Storage Assignment).

Hand-written migration: outcomes table with outcome_type ENUM, label_value
BOOLEAN NOT NULL, and (entity_id, outcome_type, evaluation_timestamp) index
per DOC-014 § Indexing Strategy.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e7e2d9a2b1"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    outcome_type_enum = sa.Enum(
        "RUG_PULL",
        "SUCCESSFUL_LAUNCH",
        "DEAD_TOKEN",
        name="outcome_type_enum",
        native_enum=True,
    )
    outcome_type_enum.create(op.get_bind(), checkfirst=True)

    op.execute(
        """
        CREATE TABLE outcomes (
            outcome_id              TEXT        PRIMARY KEY,
            schema_version          TEXT        NOT NULL DEFAULT '1.0',
            entity_id               TEXT        NOT NULL,
            outcome_type            outcome_type_enum NOT NULL,
            observation_window      TEXT        NOT NULL,
            label_definition        TEXT        NOT NULL,
            label_definition_version TEXT       NOT NULL,
            evaluation_timestamp    TIMESTAMPTZ NOT NULL,
            evaluated_at            TIMESTAMPTZ NOT NULL,
            label_value             BOOLEAN     NOT NULL,
            CONSTRAINT ck_outcome_label_value_not_null CHECK (label_value IS NOT NULL)
        )
        """
    )

    # DOC-014 § Indexing Strategy: research querying "what happened to this
    # entity" without needing every historical outcome scanned.
    op.execute(
        "CREATE INDEX ix_outcomes_entity_type_time ON outcomes "
        "(entity_id, outcome_type, evaluation_timestamp)"
    )


def downgrade() -> None:
    # Forward-only (DOC-014 § Migration Policy). A populated outcomes table
    # is not dropped by running history backward.
    pass