"""Fix sec.facts PK: add period_start to natural key (B-110).

Migration-drift cleanup found 2026-06-06. The original `sec.facts`
migration ``20260510_create_sec_schema.py`` was edited in commit
``a322aad`` (`Harden SEC ingestion and normalization`) to change the
declared PK from 5 cols ``(cik, concept, unit, period_end,
accession_number)`` to 6 cols adding ``period_start``. The same
commit bumped the normalize code's ``conflict_keys`` to match.
**Alembic doesn't re-run an edited migration** — the live
``sec.facts`` still carries the original 5-col PK, so the normalize
INSERT's 6-col ON CONFLICT specification fails to resolve, hence
the per-day ``there is no unique or exclusion constraint matching
the ON CONFLICT specification`` failure visible in
``genkei watchlist health`` for 4+ days.

**Why period_start is the right natural-key component:** 10-K filings
typically report BOTH the full-year (FY) AND the fourth-quarter (Q4)
values for the same concept (e.g. ``us-gaap:Revenues``). Both rows
share the same ``(cik, concept, unit, period_end, accession_number)``
because they come from the same filing for the same fiscal year-end
date — they differ only in ``period_start`` (Q4 starts ~3 months
before period_end; FY starts ~12 months before). Without
``period_start`` in the PK, the upsert silently drops one of the two
values. Live data verification at investigation time: **442k rows,
0 NULL period_start values, 0 collisions** on the 5-col tuple with
different period_start, so applying the 6-col PK is safe (no
existing data needs to merge / dedupe).

Strategy:
  1. SET NOT NULL on ``period_start`` (verified 0 NULL rows).
     PostgreSQL won't allow NULL columns in a PRIMARY KEY anyway, but
     making this explicit prevents future ingest paths from writing
     NULL and re-introducing the drift.
  2. DROP the existing 5-col PK constraint.
  3. ADD the 6-col PK constraint that matches the migration-file
     declaration and the normalize code's ``conflict_keys``.

TimescaleDB notes:
  * ``sec.facts`` is a hypertable partitioned on ``period_end``. The
    new PK still includes ``period_end`` so the partition column
    remains part of every unique-key check (TimescaleDB requirement).
  * No chunk decompression is required for this PK swap — the
    operation rewrites only the catalog, not row data.

Revision ID: e4c8b9d2f306
Revises: b0d2e3f4a5c7
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e4c8b9d2f306"
down_revision: str | Sequence[str] | None = "b0d2e3f4a5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Make period_start NOT NULL. Safe per investigation (0 NULLs
    #    in 442k rows on the live homelab DB); PostgreSQL would
    #    implicitly enforce NOT NULL once the column joins the PK
    #    anyway, but doing this first lets a future SELECT count
    #    surface any unexpected NULL violation cleanly.
    op.execute("ALTER TABLE sec.facts ALTER COLUMN period_start SET NOT NULL")
    # 2. Drop the existing 5-col PK.
    op.execute("ALTER TABLE sec.facts DROP CONSTRAINT facts_pkey")
    # 3. Add the 6-col PK that matches the migration-file declaration
    #    + the normalize code's conflict_keys.
    op.execute(
        "ALTER TABLE sec.facts ADD CONSTRAINT facts_pkey "
        "PRIMARY KEY (cik, concept, unit, period_start, period_end, accession_number)"
    )


def downgrade() -> None:
    # Restore the original 5-col PK + remove NOT NULL on period_start.
    # Note: any rows landed after the upgrade that have the same 5-col
    # tuple with different period_start values would PK-collide here
    # (the case the upgrade was designed to enable). Downgrade is
    # therefore only safe immediately after upgrade, before the next
    # normalize landing day; preserved for reversibility but not
    # routinely supported.
    op.execute("ALTER TABLE sec.facts DROP CONSTRAINT facts_pkey")
    op.execute(
        "ALTER TABLE sec.facts ADD CONSTRAINT facts_pkey "
        "PRIMARY KEY (cik, concept, unit, period_end, accession_number)"
    )
    op.execute("ALTER TABLE sec.facts ALTER COLUMN period_start DROP NOT NULL")
