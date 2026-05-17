"""Track normalized SEC Form 4 filings.

Revision ID: b7b944b64b78
Revises: 8a1c7d4f2b96
Create Date: 2026-05-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7b944b64b78"
down_revision: str | Sequence[str] | None = "8a1c7d4f2b96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sec.form4_normalized_filings (
            accession_number TEXT        PRIMARY KEY REFERENCES sec.filings(accession_number),
            normalized_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sec.form4_normalized_filings")
