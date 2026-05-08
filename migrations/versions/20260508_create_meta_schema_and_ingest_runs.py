"""Create meta schema and meta.ingest_runs.

First migration. Creates the operational schema that every ingester writes
into and the ingest_runs audit table that pairs with the
genkei.common.db.ingest_run() context manager.

Revision ID: 7d9d845497ae
Revises:
Create Date: 2026-05-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7d9d845497ae"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS meta")
    op.execute(
        """
        CREATE TABLE meta.ingest_runs (
            id            BIGSERIAL PRIMARY KEY,
            source        TEXT        NOT NULL,
            endpoint      TEXT,
            status        TEXT        NOT NULL,
            started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at   TIMESTAMPTZ,
            rows_written  BIGINT,
            error         TEXT,
            metadata      JSONB,
            CONSTRAINT ingest_runs_status_check
                CHECK (status IN ('running', 'success', 'failed', 'partial'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ingest_runs_source_started_idx ON meta.ingest_runs (source, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX ingest_runs_status_started_idx ON meta.ingest_runs (status, started_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meta.ingest_runs")
    op.execute("DROP SCHEMA IF EXISTS meta")
