"""Widen etf.fund_snapshots asset check to admit ZEC (B-146).

The original B-107 table pinned ``asset`` to BTC/ETH because those were the
only spot-ETF underlyings the lake tracked. B-146 added the Grayscale Zcash
Trust (ZCSH) to the ETP watchlist and taught the B-114 SEC-XBRL collector its
tags, but the collector's first live run failed on
``fund_snapshots_asset_check`` — the builder's offline tests can't see live
constraints. The check stays an explicit allowlist (a typo'd asset should
still fail loudly) and simply grows with the watchlist.

Revision ID: e7a2c48f91d3
Revises: d4f7a9c2b681
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e7a2c48f91d3"
down_revision: str | Sequence[str] | None = "d4f7a9c2b681"
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
        "CHECK (asset IN ('BTC', 'ETH', 'ZEC'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM etf.fund_snapshots WHERE asset = 'ZEC'")
    op.execute(
        "ALTER TABLE etf.fund_snapshots "
        "DROP CONSTRAINT fund_snapshots_asset_check"
    )
    op.execute(
        "ALTER TABLE etf.fund_snapshots "
        "ADD CONSTRAINT fund_snapshots_asset_check "
        "CHECK (asset IN ('BTC', 'ETH'))"
    )
