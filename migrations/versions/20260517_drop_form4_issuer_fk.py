"""Drop the issuer_cik FK on sec.form4_transactions (G-031).

Form 4s about Company B can appear in Company A's `/submissions/` index
when the filer cross-serves on multiple boards (e.g. an Alphabet
director who also sits on Ethos Technologies / LIFE filing a Form 4
about Ethos shares — SEC indexes that filing under both companies). The
XML's ``<issuerCik>`` is the authority on which company the transaction
is about; that CIK is often outside our watchlist's ``sec.companies``.

The original migration (8a1c7d4f2b96) added
``form4_transactions_issuer_cik_fkey REFERENCES sec.companies(cik)``
which fails the FK on those cross-issuer rows. We caught it the first
time the Form 4 backfill normalized blobs beyond the smoke-test set —
1,613 cached blobs hit ``ForeignKeyViolation`` for issuer_cik 1788451
(Ethos Technologies, not in our watchlist).

Resolution: drop the constraint. The index on
``(issuer_cik, transaction_date DESC)`` still supports the
common-case join with sec.companies WHERE we have the issuer. Queries
that need to scope to watchlist issuers can JOIN sec.companies; queries
that want cross-issuer signal (Phase 5 insider-network analyses) can
omit the join.

Revision ID: 2c9f5e1d3a47
Revises: b7b944b64b78
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2c9f5e1d3a47"
down_revision: str | Sequence[str] | None = "b7b944b64b78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sec.form4_transactions "
        "DROP CONSTRAINT IF EXISTS form4_transactions_issuer_cik_fkey"
    )


def downgrade() -> None:
    # Restoring the FK is only safe if every issuer_cik in the table
    # exists in sec.companies — true at original-migration time, but
    # may not be true by the time we'd ever downgrade. Use ON DELETE
    # NO ACTION (default) + accept failure on out-of-scope CIKs.
    op.execute(
        "ALTER TABLE sec.form4_transactions "
        "ADD CONSTRAINT form4_transactions_issuer_cik_fkey "
        "FOREIGN KEY (issuer_cik) REFERENCES sec.companies(cik)"
    )
