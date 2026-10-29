"""Per-source, batched retention for meta.raw_blobs (B-140b).

The D-010 job deleted everything older than a flat 90 days. GDELT's 15-minute
GKG CSVs (8-13 MB each, ~18 GB/month) made that window larger than the
Beelink disk, so the window is now a per-source map in the job's ``config``
jsonb: ``{"gdelt": "30 days", "default": "90 days"}``. The source comes from
``meta.ingest_runs.source`` via ``meta.raw_blobs.ingest_run_id``; a source
without a key falls back to ``default``, and a missing ``default`` (or no
config at all) falls back to 90 days.

GDELT raw is only a backfill cache: ``gdelt.gkg`` is the queried record and
GDELT re-serves each file for 365 days. Provenance is the GDELT URL plus
``fetched_at`` on the ingest run.

The delete is batched (2000 ids per statement, ``COMMIT`` between batches) so
the first sweep over ~21 GB does not hold one giant transaction. VACUUM cannot
run inside a procedure; autovacuum (or a manual VACUUM) reclaims pages.

Revision ID: c3e8a1f5d207
Revises: a9c4e17d3b65
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3e8a1f5d207"
down_revision: str | Sequence[str] | None = "a9c4e17d3b65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROCEDURE_NAME = "meta.delete_old_raw_blobs"
JOB_CONFIG = '{"gdelt": "30 days", "default": "90 days"}'

_UPGRADE_PROCEDURE = f"""
CREATE OR REPLACE PROCEDURE {PROCEDURE_NAME}(job_id int, config jsonb)
LANGUAGE plpgsql AS $$
DECLARE
    batch_size CONSTANT int := 2000;
    default_interval interval :=
        COALESCE((config ->> 'default')::interval, INTERVAL '90 days');
    deleted_in_batch bigint;
    batches int := 0;
    totals jsonb := '{{}}'::jsonb;
    rec record;
BEGIN
    LOOP
        deleted_in_batch := 0;
        FOR rec IN
            WITH doomed AS (
                SELECT b.id, r.source
                FROM meta.raw_blobs b
                LEFT JOIN meta.ingest_runs r ON r.id = b.ingest_run_id
                WHERE b.fetched_at < now() - COALESCE(
                    (config ->> r.source)::interval, default_interval
                )
                ORDER BY b.id
                LIMIT batch_size
            ), removed AS (
                DELETE FROM meta.raw_blobs
                WHERE id IN (SELECT id FROM doomed)
                RETURNING id
            )
            SELECT COALESCE(d.source, '(none)') AS source, count(*) AS n
            FROM doomed d
            JOIN removed USING (id)
            GROUP BY 1
        LOOP
            deleted_in_batch := deleted_in_batch + rec.n;
            totals := jsonb_set(
                totals,
                ARRAY[rec.source],
                to_jsonb(COALESCE((totals ->> rec.source)::bigint, 0) + rec.n)
            );
        END LOOP;

        EXIT WHEN deleted_in_batch = 0;
        batches := batches + 1;
        COMMIT;
    END LOOP;

    RAISE NOTICE 'delete_old_raw_blobs: % batches, rows deleted per source: %',
        batches, totals;
END;
$$;
"""

_DOWNGRADE_PROCEDURE = f"""
CREATE OR REPLACE PROCEDURE {PROCEDURE_NAME}(job_id int, config jsonb)
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM meta.raw_blobs
    WHERE fetched_at < now() - INTERVAL '90 days';
END;
$$;
"""


def _alter_job_config(config_literal: str) -> str:
    return f"""
        DO $$
        DECLARE jid int;
        BEGIN
            FOR jid IN
                SELECT job_id FROM timescaledb_information.jobs
                WHERE proc_schema = 'meta' AND proc_name = 'delete_old_raw_blobs'
            LOOP
                PERFORM alter_job(jid, config => {config_literal});
            END LOOP;
        END$$;
        """


def upgrade() -> None:
    op.execute(_UPGRADE_PROCEDURE)
    # Keep the existing daily schedule; only the config changes.
    op.execute(_alter_job_config(f"'{JOB_CONFIG}'::jsonb"))


def downgrade() -> None:
    op.execute(_DOWNGRADE_PROCEDURE)
    op.execute(_alter_job_config("NULL::jsonb"))
