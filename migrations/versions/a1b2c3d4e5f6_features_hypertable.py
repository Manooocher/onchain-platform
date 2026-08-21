"""features hypertable (DOC-012 § B.3, DOC-014 § TimescaleDB Hypertables).

Hand-written migration: features hypertable with 1-day chunks, compression
after 7 days, and (entity_id, feature_name, as_of_timestamp DESC) PIT
query index per DOC-014 § Indexing Strategy.

value is DOUBLE PRECISION — one of only two genuinely float fields (DOC-014
§ Type Mapping Rules, "Genuinely float").
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Native Postgres ENUM for entity_type (DOC-014 § Standard mappings).
    entity_type_enum = sa.Enum(
        "TRADING_PAIR", "WALLET", "TOKEN",
        name="entity_type_feature_enum",
        native_enum=True,
    )
    entity_type_enum.create(op.get_bind(), checkfirst=True)

    op.execute(
        """
        CREATE TABLE features (
            feature_id          TEXT        NOT NULL,
            schema_version      TEXT        NOT NULL DEFAULT '1.0',
            feature_name        TEXT        NOT NULL,
            entity_id           TEXT        NOT NULL,
            entity_type         TEXT        NOT NULL,
            as_of_timestamp     TIMESTAMPTZ NOT NULL,
            computed_at         TIMESTAMPTZ NOT NULL,
            "window"            TEXT,
            -- DOUBLE PRECISION — genuinely float (DOC-014 § Type Mapping
            -- Rules, DOC-012 § Clarifying an ambiguity in DOC-008).
            value               DOUBLE PRECISION NOT NULL,
            inputs              TEXT[]      NOT NULL,
            PRIMARY KEY (feature_id, as_of_timestamp)
        )
        """
    )

    # Convert to TimescaleDB hypertable, partitioned by as_of_timestamp
    # with 1-day chunks (DOC-014 § TimescaleDB Hypertables).
    op.execute(
        "SELECT create_hypertable('features', 'as_of_timestamp', "
        "chunk_time_interval => INTERVAL '1 day')"
    )

    # Compression policy: compress chunks older than 7 days (DOC-014).
    op.execute("ALTER TABLE features SET (timescaledb.compress = true)")
    op.execute("SELECT add_compression_policy('features', INTERVAL '7 days')")

    # PIT query index (DOC-014 § Indexing Strategy: "The Point-in-Time
    # pattern: the most recent value of Feature X for entity Y as of
    # timestamp T").
    op.execute(
        "CREATE INDEX ix_features_entity_name_time "
        "ON features (entity_id, feature_name, as_of_timestamp)"
    )


def downgrade() -> None:
    # Forward-only (DOC-014 § Migration Policy).
    pass
