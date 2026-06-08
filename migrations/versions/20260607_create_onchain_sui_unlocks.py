"""Create onchain.sui_unlocks per-batch table (B-089).

Lands one row per ``(allocation_name, unlock_date)`` from the CryptoRank
SUI vesting page. v1 captures only the Community Reserves allocation
(10.648% of total supply, 85 monthly batches from 2023-05 through
2030-05) — see ``docs/sources/sui-unlocks.md`` for the full Phase 1
investigation that landed on this scope. The remaining 7 SUI allocation
categories (Series A / B, Early Contributors, Mysten Labs Treasury,
Community Access Program, Stake Subsidies, Allocated After 2030) are
paywalled across the surveyed free sources and are NOT in v1.

**This table is INTENTIONALLY INCOMPLETE.** Caller-side analytics must
treat results as a partial picture, not a complete SUI unlock schedule.
Future expansion to the other 7 categories (per the "what would unblock
this" path in the survey doc) would land additional rows of the same
shape — the schema is designed to extend cleanly without migration.

Schema choice: NEW table in the existing ``onchain`` schema (alongside
B-082's ``staking_events`` and B-088's ``sui_validators``). Cross-source
queries — e.g. "is the next unlock landing during a net-outflow
staking epoch on `sui_validators`?" — don't need cross-schema joins.

Volume estimate: 85 rows for v1 (the Community Reserves schedule).
Worst-case full coverage with all 8 categories at monthly cadence
through 2030 ≈ ~700 rows total. Plain table, no indexing needed beyond
the natural-key PK.

The denormalized columns (``allocation_total_tokens``, ``allocation_total_
percent_of_supply``) are repeated across every batch within an
allocation. The duplication is intentional: ~85 rows × ~24 bytes of
overhead is trivial, and downstream queries answering "how much SUI
unlocks in the next 30 days" can sum ``unlock_tokens`` directly without
joining a separate allocations-master table. Future v2 work could
normalize if the table grows past a few thousand rows.

``unlock_tokens`` is derived at ingest time as
``allocation_total_tokens * unlock_percent_of_allocation / 100`` and
stored to keep the headline query trivial.

Revision ID: b7f8e9d0c113
Revises: b8c9d0e1f2a3
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7f8e9d0c113"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the onchain.sui_unlocks per-batch table."""
    # onchain schema already exists from B-082's migration and B-088's
    # sui_validators table; the IF NOT EXISTS defends against a fresh
    # branch where neither has been applied yet.
    op.execute("CREATE SCHEMA IF NOT EXISTS onchain")

    op.execute(
        """
        CREATE TABLE onchain.sui_unlocks (
            allocation_name                    TEXT           NOT NULL,
            unlock_date                        DATE           NOT NULL,
            allocation_total_tokens            NUMERIC(20, 0) NOT NULL,
            allocation_total_percent_of_supply NUMERIC(8, 4)  NOT NULL,
            is_tge                             BOOLEAN        NOT NULL DEFAULT FALSE,
            unlock_percent_of_allocation       NUMERIC(8, 4)  NOT NULL,
            unlock_tokens                      NUMERIC(20, 4) NOT NULL,
            vesting_type                       TEXT,
            source_endpoint                    TEXT           NOT NULL,
            fetched_at                         TIMESTAMPTZ    NOT NULL DEFAULT now(),
            ingest_run_id                      BIGINT         NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (allocation_name, unlock_date),
            CHECK (allocation_total_tokens >= 0),
            CHECK (allocation_total_percent_of_supply >= 0),
            CHECK (unlock_percent_of_allocation >= 0),
            CHECK (unlock_tokens >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX sui_unlocks_date_idx "
        "ON onchain.sui_unlocks (unlock_date)"
    )


def downgrade() -> None:
    """Drop the sui_unlocks table; leave the onchain schema intact."""
    op.execute("DROP TABLE IF EXISTS onchain.sui_unlocks")
