"""Create fred schema, fred.series, fred.observations.

First FRED schema migration (B-028). Per docs/architecture.md decisions
landed alongside this migration:

  D-013 — vintage-aware observations (PK includes realtime_start) so
          that as-of backtests can ask "what did GDP look like as of
          2023-Q1 *as known on 2023-04-15*?" without revised values
          leaking in. Each FRED revision becomes its own row.
  D-014 — single-mode ingester. FRED's /series/observations returns full
          history per call, so daily and backfill are the same code
          path. No --backfill flag needed.

Tables:
  - fred.series       entity dim, PK series_id (TEXT), holds metadata
                      (title, units, frequency, etc.)
  - fred.observations time-series fact, hypertable, PK
                      (series_id, ts, realtime_start). 90-day chunk
                      interval — most FRED series are monthly/quarterly,
                      so coarser chunks than DeFiLlama prices.

Hypertable conversion lives in this same file (greenfield table; the
storage.md "hypertable in its own migration" guidance is about not
coupling Timescale layer to *existing* table DDL).

Revision ID: e0e8baa01b39
Revises: 308b3f1284a2
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e0e8baa01b39"
down_revision: str | Sequence[str] | None = "308b3f1284a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS fred")

    op.execute(
        """
        CREATE TABLE fred.series (
            series_id           TEXT        PRIMARY KEY,
            title               TEXT,
            units               TEXT,
            units_short          TEXT,
            frequency           TEXT,
            frequency_short     TEXT,
            seasonal_adjustment TEXT,
            seasonal_adjustment_short TEXT,
            notes               TEXT,
            popularity          INTEGER,
            observation_start   DATE,
            observation_end     DATE,
            last_updated        TIMESTAMPTZ,
            source_endpoint     TEXT        NOT NULL,
            fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id       BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE fred.observations (
            series_id       TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            realtime_start  DATE        NOT NULL,
            realtime_end    DATE        NOT NULL,
            value           NUMERIC,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (series_id, ts, realtime_start)
        )
        """
    )
    op.execute("CREATE INDEX observations_ts_idx ON fred.observations (ts DESC)")
    op.execute("CREATE INDEX observations_series_ts_idx ON fred.observations (series_id, ts DESC)")

    op.execute(
        """
        SELECT create_hypertable(
            'fred.observations',
            'ts',
            chunk_time_interval => INTERVAL '90 days',
            if_not_exists => TRUE
        )
        """
    )

    # Compression policy in the same revision — FRED is small enough that
    # one migration covers the whole story (matches D-010 hygiene stance).
    op.execute(
        """
        ALTER TABLE fred.observations SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'series_id',
            timescaledb.compress_orderby = 'ts DESC, realtime_start DESC'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('fred.observations', INTERVAL '30 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('fred.observations', if_exists => TRUE)")
    op.execute("DROP TABLE IF EXISTS fred.observations")
    op.execute("DROP TABLE IF EXISTS fred.series")
    op.execute("DROP SCHEMA IF EXISTS fred")
