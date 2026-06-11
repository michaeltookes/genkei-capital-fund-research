"""Create treasury schema, treasury.series, treasury.observations (B-030).

Treasury Fiscal Data is the public-debt + debt-service companion to
FRED (rates/credit/vol/FX) and BEA (real-economy growth) already in
the lake. v1 ships the schema + four core endpoints from the
``api.fiscaldata.treasury.gov`` public API: debt outstanding
(``debt_to_penny``), Treasury operating cash balance
(``operating_cash_balance``), monthly interest expense
(``interest_expense``), and weighted-average interest rates per
security class (``avg_interest_rates``). Per-auction results
(``auctions_query``) are deferred to v2 — that endpoint is event-
shaped (one row per auction with security_type + bid_to_cover +
high_yield) and warrants a separate event table, not the time-series
schema below.

**Schema choice — latest-only, NOT vintage-aware** (matches BEA's
B-029 call, departs from FRED's D-013): Treasury's Fiscal Data API
doesn't expose a vintage-date parameter the way FRED's
``realtime_start`` does. Revisions overwrite in place at the source.
v1 matches that semantics — the PK is ``(series_id, ts)``. If we ever
need vintage-aware Treasury we add a ``fetched_at_date`` column to
the PK in a v2 migration.

**``series_id`` shape** — friendly TEXT identifier chosen in the
watchlist (e.g. ``TOTAL_PUBLIC_DEBT``, ``TGA_CLOSING_BAL``,
``AVG_RATE_BILLS``). Each series binds a specific endpoint +
value_field + optional row_filter combination. Unlike BEA where the
underlying source publishes a structured key (``T10101:1:Q``),
Treasury's API surfaces dozens of fields per row across multiple
endpoints — a human-curated key reads better at query time and stays
stable if Fiscal Data renames an internal field.

**``frequency`` NOT in PK** — every Treasury series is locked to a
single cadence by construction (``debt_to_penny`` is daily,
``interest_expense`` is monthly, etc.). Same series at multiple
cadences would be modeled as two distinct watchlist entries with
different series_ids, so the PK collision BEA worried about can't
happen here.

Tables:
  - treasury.series       entity dim, PK series_id (TEXT). Holds the
                          source endpoint, value_field, optional
                          row_filter, units, frequency, and the
                          human-readable description from the
                          watchlist.
  - treasury.observations time-series fact, hypertable on ts, PK
                          (series_id, ts). 90-day chunk interval
                          matches fred.observations + bea.observations
                          — Treasury series cover daily and monthly
                          cadences across decades of history so
                          coarser chunks make sense.

Compression: chunks > 30 days, segmentby = series_id, orderby = ts
DESC. Same shape as fred.observations + bea.observations.

Revision ID: f1a2b3c44557
Revises: e0f1a2b33446
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f1a2b3c44557"
down_revision: str | Sequence[str] | None = "e0f1a2b33446"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS treasury")

    op.execute(
        """
        CREATE TABLE treasury.series (
            series_id       TEXT        PRIMARY KEY,
            name            TEXT        NOT NULL,
            endpoint        TEXT        NOT NULL,
            value_field     TEXT        NOT NULL,
            date_field      TEXT        NOT NULL,
            row_filter      JSONB,
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
        CREATE TABLE treasury.observations (
            series_id       TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            value           NUMERIC,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (series_id, ts)
        )
        """
    )
    op.execute(
        "CREATE INDEX treasury_observations_ts_idx "
        "ON treasury.observations (ts DESC)"
    )
    op.execute(
        "CREATE INDEX treasury_observations_series_ts_idx "
        "ON treasury.observations (series_id, ts DESC)"
    )

    op.execute(
        """
        SELECT create_hypertable(
            'treasury.observations',
            'ts',
            chunk_time_interval => INTERVAL '90 days',
            if_not_exists => TRUE
        )
        """
    )

    op.execute(
        """
        ALTER TABLE treasury.observations SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'series_id',
            timescaledb.compress_orderby   = 'ts DESC'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('treasury.observations', INTERVAL '30 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    """Drop hypertable + schema (in-place hypertable revert isn't supported — G-006)."""
    op.execute(
        "SELECT remove_compression_policy('treasury.observations', if_exists => TRUE)"
    )
    op.execute("DROP TABLE IF EXISTS treasury.observations")
    op.execute("DROP TABLE IF EXISTS treasury.series")
    op.execute("DROP SCHEMA IF EXISTS treasury")
