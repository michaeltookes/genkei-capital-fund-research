"""Create meta.backup_blob_runs -- weekly raw-blob backup heartbeat (B-138).

The B-138 split posture moves ``meta.raw_blobs`` row data out of the nightly
core dump and into a weekly streamed archive on R2. That weekly path must have
its own liveness signal: writing rows to ``meta.backup_runs`` would let a
Sunday blob row mask a dead nightly core cron, while relying on host-local logs
would miss a stopped cron until a restore drill.

This table is therefore the success-only heartbeat for
``infra/backups/backup_blobs.sh``. The existing
``backup-staleness-check.yml`` workflow reads it independently from
``meta.backup_runs`` and applies a weekly threshold.

Revision ID: d4f7a9c2b681
Revises: b3c8d9e04f21
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d4f7a9c2b681"
down_revision: str | Sequence[str] | None = "b3c8d9e04f21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meta.backup_blob_runs (
            blob_backup_id   BIGSERIAL    PRIMARY KEY,
            started_at       TIMESTAMPTZ  NOT NULL,
            finished_at      TIMESTAMPTZ  NOT NULL,
            status           TEXT         NOT NULL DEFAULT 'ok' CHECK (status = 'ok'),
            blob_table       TEXT         NOT NULL,
            remote           TEXT         NOT NULL,
            archive_file     TEXT         NOT NULL,
            archive_bytes    BIGINT       NOT NULL CHECK (archive_bytes >= 0),
            duration_seconds INTEGER      NOT NULL CHECK (duration_seconds >= 0),
            host             TEXT,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX backup_blob_runs_finished_at_idx "
        "ON meta.backup_blob_runs (finished_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meta.backup_blob_runs")
