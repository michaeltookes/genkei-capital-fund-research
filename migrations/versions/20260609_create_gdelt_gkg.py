"""Create gdelt.gkg hypertable + 365-day retention (B-033).

GDELT 2.0 GKG (Global Knowledge Graph) is the firehose of news articles
tagged with themes, entities, locations, and tone. CSV files are published
every 15 min (96/day) at
``https://data.gdeltproject.org/gdeltv2/<YYYYMMDDHHMMSS>.gkg.csv.zip``.

Volume context:
- Each 15-min CSV is ~5-15MB compressed, hundreds of thousands of rows
  across the firehose, of which a watchlist-filtered slice keeps only
  articles mentioning a name we track (equities + crypto + macro series
  + protocols + filers). Realistic hit rate: low single-digit percent.
- 365-day rolling retention is the project policy (CLAUDE.md design call) —
  long enough to span every research horizon we routinely use; short
  enough to keep the table at single-digit GB. Older chunks drop via the
  TimescaleDB retention policy automatically.

Schema choice — NEW per-source ``gdelt`` schema rather than folding the
table under ``meta`` or ``analytics``:
- Per-source schemas match every other Phase 2 ingester (D-002).
- Blast radius — wiping/refilling the GDELT firehose history doesn't
  risk neighboring tables.

PK = (published_at, gkg_record_id):
- ``gkg_record_id`` (e.g. ``20260609001500-0``) is globally unique on its
  own, but TimescaleDB requires the partition column (here
  ``published_at``) to participate in every UNIQUE / PRIMARY KEY
  constraint on a hypertable. Composite PK keeps idempotency intact
  (re-running a 15-min window upserts cleanly).

``matched_assets TEXT[]`` is the watchlist filter result captured at
collect time — the canonical query is "all articles mentioning AAPL in
the last 30 days" via a GIN index. Articles with zero matches are
dropped at collect time and never land here; storing the empty-array
case would 10-100x the table for no signal.

Compression: chunks > 30 days, segmentby = source_common_name (the
common filter dimension after time), orderby published_at DESC.

Revision ID: d9f0a1b22335
Revises: c8e9f0a1b224
Create Date: 2026-06-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d9f0a1b22335"
down_revision: str | Sequence[str] | None = "c8e9f0a1b224"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RETENTION_INTERVAL = "INTERVAL '365 days'"
COMPRESS_AFTER = "INTERVAL '30 days'"
CHUNK_INTERVAL = "INTERVAL '7 days'"


def upgrade() -> None:
    """Create gdelt.gkg + hypertable + compression + 365-day retention."""
    op.execute("CREATE SCHEMA IF NOT EXISTS gdelt")

    op.execute(
        """
        CREATE TABLE gdelt.gkg (
            published_at         TIMESTAMPTZ NOT NULL,
            gkg_record_id        TEXT        NOT NULL,
            source_collection_id SMALLINT,
            source_common_name   TEXT,
            document_identifier  TEXT,
            themes               TEXT[]      NOT NULL DEFAULT '{}',
            locations            JSONB,
            persons              TEXT[]      NOT NULL DEFAULT '{}',
            organizations        TEXT[]      NOT NULL DEFAULT '{}',
            tone                 NUMERIC(7, 3),
            positive_score       NUMERIC(7, 3),
            negative_score       NUMERIC(7, 3),
            polarity             NUMERIC(7, 3),
            activity_density     NUMERIC(7, 3),
            self_density         NUMERIC(7, 3),
            word_count           INTEGER,
            matched_assets       TEXT[]      NOT NULL,
            source_endpoint      TEXT        NOT NULL,
            fetched_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id        BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (published_at, gkg_record_id),
            CHECK (cardinality(matched_assets) > 0),
            CHECK (word_count IS NULL OR word_count >= 0)
        )
        """
    )

    op.execute(
        f"SELECT create_hypertable('gdelt.gkg', 'published_at', "
        f"chunk_time_interval => {CHUNK_INTERVAL})"
    )

    op.execute(
        "CREATE INDEX gkg_themes_gin "
        "ON gdelt.gkg USING GIN (themes)"
    )
    op.execute(
        "CREATE INDEX gkg_matched_assets_gin "
        "ON gdelt.gkg USING GIN (matched_assets)"
    )
    op.execute(
        "CREATE INDEX gkg_source_published_idx "
        "ON gdelt.gkg (source_common_name, published_at DESC)"
    )

    op.execute(
        """
        ALTER TABLE gdelt.gkg SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'source_common_name',
            timescaledb.compress_orderby   = 'published_at DESC'
        )
        """
    )
    op.execute(
        f"SELECT add_compression_policy('gdelt.gkg', {COMPRESS_AFTER}, "
        f"if_not_exists => TRUE)"
    )
    op.execute(
        f"SELECT add_retention_policy('gdelt.gkg', {RETENTION_INTERVAL}, "
        f"if_not_exists => TRUE)"
    )


def downgrade() -> None:
    """Drop hypertable + schema (in-place hypertable revert isn't supported — G-006)."""
    op.execute("DROP TABLE IF EXISTS gdelt.gkg")
    op.execute("DROP SCHEMA IF EXISTS gdelt")
