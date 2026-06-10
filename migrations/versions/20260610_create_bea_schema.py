"""Create bea schema, bea.series, bea.observations (B-029).

BEA's NIPA dataset is the macro-real-economy companion to FRED's
rates/credit/vol/FX coverage already in the lake (B-028). v1 ships
the schema + the NIPA endpoint coverage; the other 12 BEA datasets
(MNE, FixedAssets, ITA, IIP, Regional, GDPbyIndustry, ...) are
deferred — every macro signal we'd actually want from BEA lives in
NIPA, and the v1 watchlist (10 curated lines per the design call)
exercises it.

**Schema choice — latest-only, NOT vintage-aware** (departs from FRED's
D-013): the BEA API doesn't expose a vintage-date parameter the way
FRED's `realtime_start` does. Revisions overwrite in place at the
source. v1 matches the source's semantics — the PK is
``(series_id, ts, frequency)``, not the 4-tuple FRED uses. If we ever
need vintage-aware BEA we add a ``fetched_at_date`` column to the PK
in a v2 migration so each ingest run snapshots a private vintage trail.

**``frequency`` in the PK** — BEA returns the same NIPA line at
quarterly and annual cadences depending on the request. Both are
research-useful (quarterly for high-frequency signals, annual for the
chart-friendly long view) and we want to keep both without one
clobbering the other. ``frequency`` is one of ``Q`` / ``A`` / ``M``
matching BEA's own labels.

**``series_id`` shape** — composite text key
``<table_id>:<line_number>:<frequency>`` (e.g. ``T10101:1:Q`` for
"Real GDP, % change SAAR, line 1" at quarterly cadence). BEA lines are
scoped per-table and per-requested cadence, so the table id + line id +
frequency is the natural unique key. Bare line numbers are not globally
unique, bare table ids don't identify a single line, and the same line
can be fetched at multiple cadences.

Tables:
  - bea.series       entity dim, PK series_id (TEXT, includes frequency).
                     Holds the human-readable description + units +
                     frequency pulled from BEA's response metadata.
  - bea.observations time-series fact, hypertable on ts, PK
                     (series_id, ts, frequency). 90-day chunk
                     interval matches fred.observations — NIPA series
                     are quarterly/annual, so coarser chunks make
                     sense.

Compression: chunks > 30 days, segmentby = series_id, orderby = ts
DESC. Same shape as fred.observations.

Revision ID: e0f1a2b33446
Revises: d9f0a1b22335
Create Date: 2026-06-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e0f1a2b33446"
down_revision: str | Sequence[str] | None = "d9f0a1b22335"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS bea")

    op.execute(
        """
        CREATE TABLE bea.series (
            series_id       TEXT        PRIMARY KEY,
            table_id        TEXT        NOT NULL,
            line_number     INTEGER     NOT NULL,
            line_description TEXT,
            series_code     TEXT,
            units           TEXT,
            frequency       TEXT,
            note_refs       TEXT[],
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            CHECK (line_number >= 0),
            CHECK (frequency IN ('Q', 'A', 'M'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE bea.observations (
            series_id       TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            frequency       TEXT        NOT NULL,
            value           NUMERIC,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (series_id, ts, frequency),
            CHECK (frequency IN ('Q', 'A', 'M'))
        )
        """
    )
    op.execute(
        "CREATE INDEX bea_observations_ts_idx "
        "ON bea.observations (ts DESC)"
    )
    op.execute(
        "CREATE INDEX bea_observations_series_ts_idx "
        "ON bea.observations (series_id, ts DESC)"
    )

    op.execute(
        """
        SELECT create_hypertable(
            'bea.observations',
            'ts',
            chunk_time_interval => INTERVAL '90 days',
            if_not_exists => TRUE
        )
        """
    )

    op.execute(
        """
        ALTER TABLE bea.observations SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'series_id',
            timescaledb.compress_orderby   = 'ts DESC'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('bea.observations', INTERVAL '30 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    """Drop hypertable + schema (in-place hypertable revert isn't supported — G-006)."""
    op.execute(
        "SELECT remove_compression_policy('bea.observations', if_exists => TRUE)"
    )
    op.execute("DROP TABLE IF EXISTS bea.observations")
    op.execute("DROP TABLE IF EXISTS bea.series")
    op.execute("DROP SCHEMA IF EXISTS bea")
