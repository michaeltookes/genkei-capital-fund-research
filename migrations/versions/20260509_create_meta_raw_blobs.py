"""Create meta.raw_blobs.

Operational table that stores the raw API payload for every endpoint a
collector hits, keyed back to the ``meta.ingest_runs`` row. Lets the
normalizer (B-018) replay any historical run without re-hitting the
upstream API and gives the audit trail a single home in Postgres
(decision in B-017: keep raw payloads in the DB rather than on disk
under ``data/raw/``).

``ON DELETE CASCADE`` on ``ingest_run_id`` is intentional — raw_blobs is
operational, not a fact table. Dropping a run drops its evidence; the
storage.md "no CASCADE to fact tables" rule applies to ``defillama.*``,
``sec.*``, etc., not ``meta.*``.

Revision ID: c4e180fcf605
Revises: 6d578bda9706
Create Date: 2026-05-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c4e180fcf605"
down_revision: str | Sequence[str] | None = "6d578bda9706"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meta.raw_blobs (
            id              BIGSERIAL    PRIMARY KEY,
            ingest_run_id   BIGINT       NOT NULL
                REFERENCES meta.ingest_runs(id) ON DELETE CASCADE,
            endpoint_name   TEXT         NOT NULL,
            url             TEXT         NOT NULL,
            payload         JSONB        NOT NULL,
            fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            UNIQUE (ingest_run_id, endpoint_name)
        )
        """
    )
    op.execute(
        "CREATE INDEX raw_blobs_endpoint_fetched_idx "
        "ON meta.raw_blobs (endpoint_name, fetched_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meta.raw_blobs")
