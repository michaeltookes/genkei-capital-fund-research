"""Create eia schema, eia.series, eia.observations (B-032).

EIA's Open Data v2 API is the energy companion to FRED (rates / credit / FX
/ vol), BEA (real-economy growth), and Treasury (debt / cash / cost-of-debt)
already in the lake. v1 ships the schema + ~10 core series across petroleum
(WTI / Brent spot, commercial + SPR crude inventories, gasoline & distillate
inventories, US crude production), natural gas (Henry Hub spot, Lower-48
working storage, marketed production), and electricity (US net generation).

**Schema choice — latest-only, NOT vintage-aware** (matches BEA's B-029 +
Treasury's B-030 calls, departs from FRED's D-013): EIA's v2 API does not
expose a vintage / as-of parameter — revisions overwrite in place at the
source (esp. monthly STEO + production data). v1 matches that semantics —
the PK is ``(series_id, ts)``. If vintage-aware EIA is ever needed we add a
``fetched_at_date`` column to the PK in a v2 migration.

**``series_id`` shape** — friendly TEXT identifier curated in the watchlist
(e.g. ``WTI_SPOT``, ``HH_SPOT``, ``CRUDE_INV_EXSPR``). Each watchlist entry
binds a (route + frequency + facets + data_field) tuple to one ``series_id``.
EIA v2's native facet-based query model means there's no single canonical
upstream identifier — the legacy series IDs (``RWTC``, ``WCESTUS1``) live as
a facet value, and not every series has one. A friendly local key is more
stable across EIA's API evolution.

**``frequency`` NOT in PK** — every EIA series is locked to a single cadence
by construction (each watchlist entry pins one ``frequency``). Same series at
two cadences (e.g. daily WTI + monthly WTI average) would be two distinct
watchlist entries with distinct series_ids, so the PK collision BEA worried
about can't happen here.

Tables:
  - eia.series       entity dim, PK series_id (TEXT). Holds the EIA v2 route,
                     frequency, data_field, JSONB facets (the filter that
                     selects this specific series within the route), units,
                     date_field, and human-readable description from the
                     watchlist.
  - eia.observations time-series fact, hypertable on ts, PK (series_id, ts).
                     90-day chunk interval matches fred.observations,
                     bea.observations, and treasury.observations — EIA series
                     span daily (price, demand), weekly (inventories),
                     monthly (production), and longer cadences across
                     decades of history so coarser chunks make sense.

Compression: chunks > 30 days, segmentby = series_id, orderby = ts DESC.
Same shape as fred / bea / treasury observation tables.

Revision ID: a2b3c4d55667
Revises: f1a2b3c44557
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a2b3c4d55667"
down_revision: str | Sequence[str] | None = "f1a2b3c44557"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS eia")

    op.execute(
        """
        CREATE TABLE eia.series (
            series_id       TEXT        PRIMARY KEY,
            name            TEXT        NOT NULL,
            route           TEXT        NOT NULL,
            data_field      TEXT        NOT NULL,
            date_field      TEXT        NOT NULL,
            facets          JSONB,
            units           TEXT,
            frequency       TEXT        NOT NULL,
            rationale       TEXT,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            CHECK (frequency IN ('D', 'W', 'M', 'Q', 'A'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE eia.observations (
            series_id       TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            value           NUMERIC,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            FOREIGN KEY (series_id) REFERENCES eia.series(series_id),
            PRIMARY KEY (series_id, ts)
        )
        """
    )
    op.execute(
        "CREATE INDEX eia_observations_ts_idx ON eia.observations (ts DESC)"
    )
    op.execute(
        "CREATE INDEX eia_observations_series_ts_idx "
        "ON eia.observations (series_id, ts DESC)"
    )

    op.execute(
        """
        SELECT create_hypertable(
            'eia.observations',
            'ts',
            chunk_time_interval => INTERVAL '90 days',
            if_not_exists => TRUE
        )
        """
    )

    op.execute(
        """
        ALTER TABLE eia.observations SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'series_id',
            timescaledb.compress_orderby   = 'ts DESC'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('eia.observations', INTERVAL '30 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    """Drop hypertable + schema (in-place hypertable revert isn't supported — G-006)."""
    op.execute(
        "SELECT remove_compression_policy('eia.observations', if_exists => TRUE)"
    )
    op.execute("DROP TABLE IF EXISTS eia.observations")
    op.execute("DROP TABLE IF EXISTS eia.series")
    op.execute("DROP SCHEMA IF EXISTS eia")
