"""Allow NEAR checkpoints in etf.fund_snapshots.

The CIK-tagged NRR watchlist entry joins the SEC ETF collector's bulk insert.
Its first checkpoint must not fail the asset check and roll back other funds.
Keep the explicit allowlist so unsupported assets still fail loudly.

Revision ID: f8b3d59a02e4
Revises: e7a2c48f91d3
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f8b3d59a02e4"
down_revision: str | Sequence[str] | None = "e7a2c48f91d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE etf.fund_snapshots "
        "DROP CONSTRAINT fund_snapshots_asset_check"
    )
    op.execute(
        "ALTER TABLE etf.fund_snapshots "
        "ADD CONSTRAINT fund_snapshots_asset_check "
        "CHECK (asset IN ('BTC', 'ETH', 'ZEC', 'NEAR'))"
    )


def downgrade() -> None:
    # Match the previous widening migration: remove newly supported rows
    # before restoring the narrower constraint; preserve all other assets.
    op.execute("DELETE FROM etf.fund_snapshots WHERE asset = 'NEAR'")
    op.execute(
        "ALTER TABLE etf.fund_snapshots "
        "DROP CONSTRAINT fund_snapshots_asset_check"
    )
    op.execute(
        "ALTER TABLE etf.fund_snapshots "
        "ADD CONSTRAINT fund_snapshots_asset_check "
        "CHECK (asset IN ('BTC', 'ETH', 'ZEC'))"
    )
