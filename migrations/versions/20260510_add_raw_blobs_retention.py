"""Add 90-day retention on meta.raw_blobs.

D-010 committed to retention as Phase 1 hygiene. Once a normalizer run
consumes a raw blob, the normalized rows in defillama.* are the system
of record; raw_blobs is just audit/replay. Keeping 90 days gives us
plenty of window to debug recent runs without the JSONB store growing
unbounded.

Implementation: TimescaleDB's add_job runs a stored procedure on a
schedule using the in-database background worker — no external cron
needed, survives container restarts. Not a hypertable retention policy
because raw_blobs isn't a hypertable (its PK is `id` and we want the
UNIQUE(ingest_run_id, endpoint_name) idempotency constraint, which
doesn't compose cleanly with a fetched_at-keyed hypertable).

Revision ID: 308b3f1284a2
Revises: ef4af7ae37bb
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "308b3f1284a2"
down_revision: str | Sequence[str] | None = "ef4af7ae37bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROCEDURE_NAME = "meta.delete_old_raw_blobs"
RETENTION_INTERVAL = "INTERVAL '90 days'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE PROCEDURE {PROCEDURE_NAME}(job_id int, config jsonb)
        LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM meta.raw_blobs
            WHERE fetched_at < now() - {RETENTION_INTERVAL};
        END;
        $$;
        """
    )
    # Daily schedule — no need to be aggressive here, blobs accumulate slowly.
    op.execute(f"SELECT add_job('{PROCEDURE_NAME}', '1 day')")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE jid int;
        BEGIN
            FOR jid IN
                SELECT job_id FROM timescaledb_information.jobs
                WHERE proc_schema = 'meta' AND proc_name = 'delete_old_raw_blobs'
            LOOP
                PERFORM delete_job(jid);
            END LOOP;
        END$$;
        """
    )
    op.execute(f"DROP PROCEDURE IF EXISTS {PROCEDURE_NAME}(int, jsonb)")
