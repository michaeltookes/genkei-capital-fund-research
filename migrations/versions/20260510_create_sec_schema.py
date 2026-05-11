"""Create sec schema, sec.companies, sec.filings, sec.facts.

First SEC schema migration (B-027 option B). Lands the metadata layer
(submissions index) and the XBRL company-facts layer in one go. Form 4
and 13F document parsing are explicit follow-ups (B-079, B-080).

Tables:
  - sec.companies  Entity dim. PK cik (zero-padded 10-char). Holds
                   ticker / name / SIC / exchanges / fiscal-year-end
                   metadata from /submissions/CIK{cik}.json.
  - sec.filings    One row per SEC filing. PK accession_number (the
                   SEC's own unique filing ID). Indexed on
                   (cik, filed_at DESC) and (form_type, filed_at DESC)
                   for the common query patterns. Not a hypertable —
                   28 watchlist companies × ~100 filings/year × 30 years
                   = ~85k rows steady-state, well within plain-PG range.
  - sec.facts      XBRL fact table. Hypertable on period_end (30-day
                   chunks), compression on chunks > 30 days old. PK
                   (cik, concept, unit, period_start, period_end, accession_number)
                   — same fact can be reported by both the original
                   10-Q and the subsequent 10-K, so accession_number
                   is part of the PK to capture both. Millions of rows
                   at scale (28 companies × 30 years × ~hundreds of
                   concepts per filing × multiple filings per concept).

Revision ID: 0f3acd7fbf46
Revises: e0e8baa01b39
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0f3acd7fbf46"
down_revision: str | Sequence[str] | None = "e0e8baa01b39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS sec")

    op.execute(
        """
        CREATE TABLE sec.companies (
            cik              TEXT        PRIMARY KEY,
            ticker           TEXT,
            name             TEXT        NOT NULL,
            sic              TEXT,
            sic_description  TEXT,
            ein              TEXT,
            entity_type      TEXT,
            fiscal_year_end  TEXT,
            exchanges        TEXT[],
            former_names     JSONB,
            source_endpoint  TEXT        NOT NULL,
            fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )
    op.execute("CREATE INDEX companies_ticker_idx ON sec.companies (ticker)")

    op.execute(
        """
        CREATE TABLE sec.filings (
            accession_number       TEXT        PRIMARY KEY,
            cik                    TEXT        NOT NULL REFERENCES sec.companies(cik),
            form_type              TEXT        NOT NULL,
            filed_at               DATE        NOT NULL,
            accepted_at            TIMESTAMPTZ,
            report_date            DATE,
            primary_document       TEXT,
            primary_doc_description TEXT,
            file_number            TEXT,
            film_number            TEXT,
            items                  TEXT,
            size_bytes             BIGINT,
            is_xbrl                BOOLEAN,
            is_inline_xbrl         BOOLEAN,
            source_endpoint        TEXT        NOT NULL,
            fetched_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id          BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )
    op.execute("CREATE INDEX filings_cik_filed_idx ON sec.filings (cik, filed_at DESC)")
    op.execute("CREATE INDEX filings_form_filed_idx ON sec.filings (form_type, filed_at DESC)")

    op.execute(
        """
        CREATE TABLE sec.facts (
            cik              TEXT        NOT NULL REFERENCES sec.companies(cik),
            taxonomy         TEXT        NOT NULL,
            concept          TEXT        NOT NULL,
            unit             TEXT        NOT NULL,
            period_start     DATE,
            period_end       DATE        NOT NULL,
            value            NUMERIC,
            accession_number TEXT        NOT NULL,
            form_type        TEXT,
            filed_at         DATE,
            frame            TEXT,
            fy               INTEGER,
            fp               TEXT,
            source_endpoint  TEXT        NOT NULL,
            fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (cik, concept, unit, period_start, period_end, accession_number)
        )
        """
    )
    op.execute("CREATE INDEX facts_concept_period_idx ON sec.facts (concept, period_end DESC)")
    op.execute(
        "CREATE INDEX facts_cik_concept_period_idx ON sec.facts (cik, concept, period_end DESC)"
    )

    op.execute(
        """
        SELECT create_hypertable(
            'sec.facts',
            'period_end',
            chunk_time_interval => INTERVAL '30 days',
            if_not_exists => TRUE
        )
        """
    )

    op.execute(
        """
        ALTER TABLE sec.facts SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'cik, concept',
            timescaledb.compress_orderby = 'period_end DESC, accession_number'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('sec.facts', INTERVAL '30 days', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('sec.facts', if_exists => TRUE)")
    op.execute("DROP TABLE IF EXISTS sec.facts")
    op.execute("DROP TABLE IF EXISTS sec.filings")
    op.execute("DROP TABLE IF EXISTS sec.companies")
    op.execute("DROP SCHEMA IF EXISTS sec")
