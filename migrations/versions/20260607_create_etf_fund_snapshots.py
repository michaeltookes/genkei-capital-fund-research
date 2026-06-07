"""Create etf schema + etf.fund_snapshots table (B-107).

The iShares product-screener JSON feed (see ``docs/sources/spot-etf-net-flow.md``)
publishes daily NAV + total net assets for IBIT, ETHA, and ETHB. v1 of B-107
lands one row per ``(ticker, snapshot_date)`` from that feed; daily net flow
is DERIVED at query time via SQL window functions rather than stored, so the
table is a pure raw-snapshot table with no recomputation race conditions when
backfilling or when a snapshot is republished.

Shape:
  - ``etf.fund_snapshots``  Plain table. Volume estimate: 3 funds × 252 trading
                            days/year × 5y horizon ≈ 4k rows steady-state —
                            tiny, same call B-031 (cftc.cot_reports) made.

Why ``etf`` not ``ishares``. Source-named schemas (``coinbase``, ``coingecko``,
``cftc``, ``yahoo``) work when one source maps to one fact stream. ETF flow
data will land from multiple issuers as v2.1 expands (BITB / FBTC / GBTC),
and merging them into a single ``etf.fund_snapshots`` table — with the issuer
deducible from the ticker via the watchlist — keeps cross-issuer queries from
needing a UNION. A future ``etf.holdings`` or ``etf.creations_redemptions``
table sits naturally in the same schema.

Why store shares-outstanding when it's derivable. ``shares_outstanding =
total_net_assets_usd / nav_per_share_usd`` algebraically; we could compute on
read. But (a) callers querying for flow need it on every row and a divide-on-read
adds latency, (b) iShares publishes both NAV and TNA from the same source so
storing both is auditable independently, and (c) the rounding floor on
``total_net_assets`` ($1) means dividing TNA by NAV loses precision vs. iShares'
own internal share-count — storing both lets us treat the derived shares as a
"best-available" number with the raw inputs visible.

NAV is stored as ``NUMERIC(20, 8)``: spot crypto ETF NAVs in 2024-2026 sit in
the ~$10-$60 range but ETHB at $0.20-ish per share could appear with future
launches, and the 8 fractional digits cover the standard NAV precision iShares
publishes. ``total_net_assets_usd`` as ``NUMERIC(20, 2)`` because TNA is always
reported to the dollar (no fractional cents). ``shares_outstanding`` as
``NUMERIC(20, 4)`` — share counts are integers in concept but the divide-derived
value can drift by fractional amounts due to TNA rounding, so 4 decimals keeps
the rounding error visible without overpromising precision.

Revision ID: f5d9c0e1a407
Revises: e4c8b9d2f306
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f5d9c0e1a407"
down_revision: str | Sequence[str] | None = "e4c8b9d2f306"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the etf schema, fund_snapshots table, and lookup index."""
    op.execute("CREATE SCHEMA IF NOT EXISTS etf")

    op.execute(
        """
        CREATE TABLE etf.fund_snapshots (
            ticker                 TEXT           NOT NULL,
            snapshot_date          DATE           NOT NULL,
            issuer                 TEXT           NOT NULL,
            asset                  TEXT           NOT NULL,
            cusip                  TEXT,
            isin                   TEXT,
            nav_per_share_usd      NUMERIC(20, 8) NOT NULL,
            total_net_assets_usd   NUMERIC(20, 2) NOT NULL,
            shares_outstanding     NUMERIC(20, 4) NOT NULL,
            source_endpoint        TEXT           NOT NULL,
            fetched_at             TIMESTAMPTZ    NOT NULL DEFAULT now(),
            ingest_run_id          BIGINT         NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (ticker, snapshot_date),
            CHECK (asset IN ('BTC', 'ETH')),
            CHECK (nav_per_share_usd > 0),
            CHECK (total_net_assets_usd >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX fund_snapshots_asset_date_idx "
        "ON etf.fund_snapshots (asset, snapshot_date DESC)"
    )


def downgrade() -> None:
    """Drop the etf snapshot table and schema."""
    op.execute("DROP TABLE IF EXISTS etf.fund_snapshots")
    op.execute("DROP SCHEMA IF EXISTS etf")
