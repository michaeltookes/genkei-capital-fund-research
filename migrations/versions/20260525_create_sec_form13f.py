"""Create sec.filers + sec.form13f_filings + sec.form13f_holdings (B-080).

13F is the quarterly institutional-holdings report (Form 13F-HR / 13F-HR/A
for the actual holdings, 13F-NT / 13F-NT/A for "notice" filings where the
manager is reporting via an aggregate / affiliated filer). The B-079 Form 4
ingester gave us insider flow on the *issuer* side; 13F gives us
institutional positioning on the *filer* side — the complementary piece
needed for crowding monitors (B-061) and conviction-cluster context.

Four new tables:

  - sec.filers                       Entity dim for 13F filers (institutional
                                     managers). Distinct from sec.companies
                                     because a filer is rarely also an issuer
                                     in our equity watchlist (Berkshire is the
                                     exception, not the rule), and even when
                                     both exist the manager CIK differs from
                                     the issuer CIK. PK ``filer_cik`` (zero-
                                     padded 10-char).

  - sec.form13f_filings              One row per 13F filing. PK
                                     ``accession_number``. FK to
                                     ``sec.filers``. Holds form_type, the
                                     period_of_report (CCYY-MM-DD of quarter
                                     end), report_type (HOLDINGS REPORT /
                                     NOTICE / etc.), and any ``other_managers``
                                     cross-references (for 13F-NT amendments
                                     that link back to an aggregate 13F-HR).

  - sec.form13f_holdings             One row per *position* inside a 13F-HR
                                     information table. PK
                                     ``(accession_number, holding_idx)`` where
                                     holding_idx is the 0-based position
                                     within the filing. value_usd is in
                                     **dollars** (×1000 conversion applied at
                                     normalize time — the canonical 13F
                                     gotcha lives in the docstring, the
                                     column name carries the canonical unit).

  - sec.form13f_normalized_filings   Mirrors sec.form4_normalized_filings:
                                     accession-level marker for which 13F
                                     filings have already been normalized.
                                     Needed because 13F-NT (notice-only)
                                     filings produce zero ``holdings`` rows
                                     but still need to be marked processed
                                     so the normalizer doesn't keep retrying
                                     them forever.

Not hypertables — at ~10 watchlist filers × 4 quarters/year × ~30 years ≈
1.2k filings and ~150k holdings rows steady-state, well within plain-PG
range. Same call we made for ``sec.filings`` (see 20260510_create_sec_schema).

Indexes:
  - sec.form13f_filings (filer_cik, period_of_report DESC)
  - sec.form13f_holdings (filer_cik, period_of_report DESC)  — "what does Berkshire hold this quarter"
  - sec.form13f_holdings (cusip, period_of_report DESC)      — "who holds CUSIP X"

Revision ID: a1f3e8d20571
Revises: f5b2c8d3e914
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a1f3e8d20571"
down_revision: str | Sequence[str] | None = "f5b2c8d3e914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sec.filers (
            filer_cik        TEXT        PRIMARY KEY,
            name             TEXT        NOT NULL,
            source_endpoint  TEXT        NOT NULL,
            first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE sec.form13f_filings (
            accession_number       TEXT        PRIMARY KEY,
            filer_cik              TEXT        NOT NULL REFERENCES sec.filers(filer_cik),
            form_type              TEXT        NOT NULL,
            filed_at               DATE        NOT NULL,
            accepted_at            TIMESTAMPTZ,
            period_of_report       DATE,
            report_type            TEXT,
            primary_document       TEXT,
            primary_doc_description TEXT,
            other_managers         JSONB,
            source_endpoint        TEXT        NOT NULL,
            fetched_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id          BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX form13f_filings_filer_period_idx "
        "ON sec.form13f_filings (filer_cik, period_of_report DESC)"
    )
    op.execute(
        "CREATE INDEX form13f_filings_form_filed_idx "
        "ON sec.form13f_filings (form_type, filed_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE sec.form13f_holdings (
            accession_number          TEXT        NOT NULL REFERENCES sec.form13f_filings(accession_number),
            holding_idx               INTEGER     NOT NULL,
            filer_cik                 TEXT        NOT NULL REFERENCES sec.filers(filer_cik),
            period_of_report          DATE        NOT NULL,
            cusip                     TEXT        NOT NULL,
            issuer_name               TEXT,
            class_title               TEXT,
            value_usd                 NUMERIC,
            shares_or_principal       NUMERIC,
            shares_or_principal_type  TEXT,
            put_call                  TEXT,
            investment_discretion     TEXT,
            other_managers            TEXT,
            voting_authority_sole     NUMERIC,
            voting_authority_shared   NUMERIC,
            voting_authority_none     NUMERIC,
            source_endpoint           TEXT        NOT NULL,
            fetched_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id             BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (accession_number, holding_idx)
        )
        """
    )
    op.execute(
        "CREATE INDEX form13f_holdings_filer_period_idx "
        "ON sec.form13f_holdings (filer_cik, period_of_report DESC)"
    )
    op.execute(
        "CREATE INDEX form13f_holdings_cusip_period_idx "
        "ON sec.form13f_holdings (cusip, period_of_report DESC)"
    )

    op.execute(
        """
        CREATE TABLE sec.form13f_normalized_filings (
            accession_number TEXT        PRIMARY KEY REFERENCES sec.form13f_filings(accession_number),
            normalized_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sec.form13f_normalized_filings")
    op.execute("DROP TABLE IF EXISTS sec.form13f_holdings")
    op.execute("DROP TABLE IF EXISTS sec.form13f_filings")
    op.execute("DROP TABLE IF EXISTS sec.filers")
