"""Install TimescaleDB extension.

Activates TimescaleDB on the database. Per docs/storage.md and
docs/infrastructure.md, the homelab container image is swapped from
postgres:16-alpine to timescale/timescaledb:latest-pg16 (B-007); this
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


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")


def downgrade() -> None:
    # Idempotent — extension may not exist if a prior environment never
    # swapped to the timescale image. CASCADE intentionally omitted so
    # downgrade fails if hypertables exist (caller drops those first).
    op.execute("DROP EXTENSION IF EXISTS timescaledb")
