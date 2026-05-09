"""Install TimescaleDB extension.

Activates TimescaleDB on the database. Per docs/storage.md and
docs/infrastructure.md, the homelab container image is swapped from
postgres:16-alpine to timescale/timescaledb:2.26.4-pg16 (B-007); this
migration runs against the swapped image to enable the extension.

If applied against a vanilla Postgres image (no timescaledb shared
library), the CREATE EXTENSION statement fails loudly — which is the
desired behavior. Don't silently degrade.

Revision ID: 69f3fe427252
Revises: 7d9d845497ae
Create Date: 2026-05-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "69f3fe427252"
down_revision: str | Sequence[str] | None = "7d9d845497ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSION_COMMENT = "created by Alembic revision 69f3fe427252"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
            ) THEN
                EXECUTE 'CREATE EXTENSION timescaledb';
                EXECUTE 'COMMENT ON EXTENSION timescaledb IS ''{EXTENSION_COMMENT}''';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Only drop the extension if this revision created it. If TimescaleDB
    # already existed before upgrade(), preserve it on downgrade.
    op.execute(
        f"""
        DO $$
        DECLARE
            extension_oid oid;
        BEGIN
            SELECT oid INTO extension_oid
            FROM pg_extension
            WHERE extname = 'timescaledb';

            IF extension_oid IS NOT NULL
                AND obj_description(extension_oid, 'pg_extension') = '{EXTENSION_COMMENT}'
            THEN
                EXECUTE 'DROP EXTENSION timescaledb';
            END IF;
        END
        $$;
        """
    )
