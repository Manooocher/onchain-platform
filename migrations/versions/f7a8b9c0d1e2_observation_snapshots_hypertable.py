"""observation_snapshots hypertable (DOC-012 § B.3, DOC-014 § TimescaleDB
Hypertables).

Hand-written migration: observation_snapshots hypertable with 1-day chunks,
compression after 7 days, and (entity_id, snapshot_timestamp DESC) index
per DOC-014 § Indexing Strategy.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE observation_snapshots (
            snapshot_id         TEXT        NOT NULL,
            schema_version      TEXT        NOT NULL DEFAULT '1.0',
            entity_id           TEXT        NOT NULL,
            chain_id            BIGINT      NOT NULL,
            snapshot_timestamp  TIMESTAMPTZ NOT NULL,
            observed_at         TIMESTAMPTZ NOT NULL,
            ingested_at         TIMESTAMPTZ NOT NULL,
            source              TEXT        NOT NULL,
            snapshot_version    INTEGER     NOT NULL DEFAULT 1,
            reserve0            NUMERIC     NOT NULL,
            reserve1            NUMERIC     NOT NULL,
            price               NUMERIC     NOT NULL,
            liquidity_usd       NUMERIC,
            holder_count        INTEGER,
            market_cap_usd      NUMERIC,
            fdv_usd             NUMERIC
            ,
            PRIMARY KEY (snapshot_id, snapshot_timestamp)
        )
        """
    )

    # Convert to TimescaleDB hypertable, partitioned by snapshot_timestamp
    # with 1-day chunks (DOC-014 § TimescaleDB Hypertables).
    op.execute(
        "SELECT create_hypertable('observation_snapshots', 'snapshot_timestamp', "
        "chunk_time_interval => INTERVAL '1 day')"
    )

    # Compression policy: compress chunks older than 7 days (DOC-014).
    op.execute(
        "ALTER TABLE observation_snapshots SET (timescaledb.compress = true)"
    )
    op.execute(
        "SELECT add_compression_policy('observation_snapshots', INTERVAL '7 days')"
    )

    # PIT query index (DOC-014 § Indexing Strategy).
    op.execute(
        "CREATE INDEX ix_observation_snapshots_entity_time "
        "ON observation_snapshots (entity_id, snapshot_timestamp DESC)"
    )


def downgrade() -> None:
    # Forward-only (DOC-014 § Migration Policy).
    pass
