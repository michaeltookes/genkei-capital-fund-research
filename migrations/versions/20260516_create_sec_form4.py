"""Create sec.insiders + sec.form4_transactions (B-079).

Form 4 insider-transactions schema. Two tables:

  - sec.insiders            Entity dim for reporting owners. PK
                            reporter_cik (SEC assigns CIKs to filers
                            too, not just issuers). Tiny table — at our
                            watchlist scope (28 issuers), expect a few
                            thousand insiders total.

  - sec.form4_transactions  One row per *transaction* inside a Form 4
                            filing. A single filing can carry multiple
                            transactions (e.g. an officer reports two
                            sales on the same day plus an option
                            exercise). PK (accession_number,
                            transaction_idx) where transaction_idx is
                            the 0-based position within the filing.

Not a hypertable — at 28 watchlist issuers × ~50 filings/year × ~1-3
transactions per filing × 30 years ≈ 50-100k rows steady-state, well
within plain-PG range. Same call sec.filings made for the same reason
(see 20260510_create_sec_schema.py header).

Indexes:
  - (issuer_cik, transaction_date DESC)   common case: "AAPL recent insiders"
  - (reporter_cik, transaction_date DESC) common case: "this insider across companies"

The transaction_code column carries the SEC's single-letter code:
P=open-market purchase, S=open-market sale, A=grant/award (compensation),
F=tax-withholding sale, M=option exercise, G=gift, J=other, etc.
acquired_disposed records the same direction at amount-level ('A' or 'D').

Revision ID: 8a1c7d4f2b96
Revises: 05c48dd08fb0
Create Date: 2026-05-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8a1c7d4f2b96"
down_revision: str | Sequence[str] | None = "05c48dd08fb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sec.insiders (
            reporter_cik     TEXT        PRIMARY KEY,
            reporter_name    TEXT        NOT NULL,
            source_endpoint  TEXT        NOT NULL,
            first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE sec.form4_transactions (
            accession_number             TEXT        NOT NULL REFERENCES sec.filings(accession_number),
            transaction_idx              INTEGER     NOT NULL,
            issuer_cik                   TEXT        NOT NULL REFERENCES sec.companies(cik),
            reporter_cik                 TEXT        NOT NULL REFERENCES sec.insiders(reporter_cik),
            is_director                  BOOLEAN,
            is_officer                   BOOLEAN,
            is_ten_percent_owner         BOOLEAN,
            is_other                     BOOLEAN,
            officer_title                TEXT,
            other_text                   TEXT,
            period_of_report             DATE,
            transaction_date             DATE        NOT NULL,
            transaction_code             TEXT,
            acquired_disposed            TEXT,
            security_title               TEXT,
            is_derivative                BOOLEAN     NOT NULL,
            shares                       NUMERIC,
            price_usd                    NUMERIC,
            post_transaction_shares      NUMERIC,
            ownership_type               TEXT,
            underlying_security_title    TEXT,
            underlying_shares            NUMERIC,
            conversion_or_exercise_price NUMERIC,
            exercise_date                DATE,
            expiration_date              DATE,
            source_endpoint              TEXT        NOT NULL,
            fetched_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id                BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (accession_number, transaction_idx)
        )
        """
    )
    op.execute(
        "CREATE INDEX form4_issuer_date_idx "
        "ON sec.form4_transactions (issuer_cik, transaction_date DESC)"
    )
    op.execute(
        "CREATE INDEX form4_reporter_date_idx "
        "ON sec.form4_transactions (reporter_cik, transaction_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sec.form4_transactions")
    op.execute("DROP TABLE IF EXISTS sec.insiders")
