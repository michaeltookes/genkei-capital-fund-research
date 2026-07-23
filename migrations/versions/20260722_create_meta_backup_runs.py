"""Create meta.backup_runs — nightly-backup heartbeat (B-138).

The nightly ``infra/backups/backup_postgres.sh`` runs as *host cron on the
Beelink*, outside GitHub Actions — so neither ``workflow-failure-alert.yml``
(watches Actions runs) nor the self-hosted runner (mounts no host path, only
reaches Postgres over ``mission_control_net``) can see whether a backup ran.
This table is the bridge: the backup script writes one row here on each
successful dump, and ``backup-staleness-check.yml`` reads the newest row over
the same network path ``genkei watchlist health`` already uses. A missing or
stale row is exactly the "cron silently stopped" failure mode B-119 exists to
catch — the counterpart, on the backup side, to what
``ingest-staleness-check.yml`` does for ingest.

Schema design choices:

  * **``backup_id BIGSERIAL`` surrogate PK** — same convention as
    ``meta.signal_events`` / ``meta.alerts``. No natural key worth enforcing;
    each run is its own row and the monitor only ever reads the latest.
  * **Success-only rows.** The script inserts *after* the dump verifies, so a
    row's existence means "a good dump landed." A failed backup writes no row
    (and may not reach the DB at all — e.g. the container is down), so the
    absence-of-recent-row is the signal; the script's own Discord post covers
    the ran-and-errored case. ``status`` is kept as a column (not hardcoded)
    so a future partial/degraded state has somewhere to live.
  * **``offsite_status``** — ``uploaded`` / ``skipped`` / ``failed`` so the
    monitor can distinguish "local dump fine but off-site copy silently
    stopped" from a healthy run. Nullable for pre-off-site history.
  * **``dump_bytes`` / ``duration_seconds``** — cheap provenance; lets a query
    spot a dump that suddenly halves in size (truncation) or balloons in time.
  * **Plain table, not a hypertable.** One row per night — chunking never pays.
    Same call ``meta.alerts`` made.

Indexes:
  * ``(finished_at DESC)`` — the monitor's only query ("newest successful run").

Revision ID: b3c8d9e04f21
Revises: a2b7c8d09e13
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b3c8d9e04f21"
down_revision: str | Sequence[str] | None = "a2b7c8d09e13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meta.backup_runs (
            backup_id        BIGSERIAL    PRIMARY KEY,
            started_at       TIMESTAMPTZ  NOT NULL,
            finished_at      TIMESTAMPTZ  NOT NULL,
            status           TEXT         NOT NULL DEFAULT 'ok' CHECK (
                                 status IN ('ok', 'degraded', 'failed')
                             ),
            dump_file        TEXT         NOT NULL,
            dump_bytes       BIGINT       NOT NULL CHECK (dump_bytes >= 0),
            duration_seconds INTEGER      NOT NULL CHECK (duration_seconds >= 0),
            offsite_status   TEXT         CHECK (
                                 offsite_status IN ('uploaded', 'skipped', 'failed')
                             ),
            host             TEXT,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX backup_runs_finished_at_idx "
        "ON meta.backup_runs (finished_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meta.backup_runs")
