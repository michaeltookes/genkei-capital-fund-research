"""Create analytics.crypto_relative_strength view (B-090).

Phase 6 derived view that emits the asset-vs-peer relative-strength
metric for every pair of coingecko coins and a fixed set of trailing
windows (7d / 30d / 90d / 180d / 365d). Lives in the ``analytics``
schema per ``docs/storage.md``'s convention for cross-source
materialized views — though this one is a *live* view (not
materialized), because the math is cheap (one indexed lookup per
(coingecko_id, window_days), then a self-cross-join on the returns
CTE) and a live view avoids the freshness-policy headache.

The math, per (asset, peer, window_days):
  asset_return_pct = 100 * (asset_latest - asset_lookback) / asset_lookback
  peer_return_pct  = 100 * (peer_latest  - peer_lookback)  / peer_lookback
  relative_strength_pct = asset_return_pct - peer_return_pct

``lookback`` is the most recent price at-or-before ``latest_ts -
window_days``. Each (coingecko_id) anchors on its own latest_ts so
late-arriving rows on one coin don't drift the others.

NULL semantics: if either side's lookback is missing (asset newer
than the window — e.g. a token only listed 30 days ago has no 90d
return), ``relative_strength_pct`` is NULL. Downstream consumers
read this as "insufficient history" rather than zero.

Revision ID: b1c2d3e4f5a6
Revises: 7e4d2a1f8b35
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "7e4d2a1f8b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")

    op.execute(
        """
        CREATE OR REPLACE VIEW analytics.crypto_relative_strength AS
        WITH windows AS (
            SELECT * FROM (VALUES (7), (30), (90), (180), (365)) AS w(window_days)
        ),
        latest AS (
            SELECT DISTINCT ON (coingecko_id)
                coingecko_id,
                ts AS latest_ts,
                price_usd AS latest_price
            FROM coingecko.market_data
            WHERE price_usd IS NOT NULL
            ORDER BY coingecko_id, ts DESC
        ),
        lookback AS (
            SELECT
                l.coingecko_id,
                w.window_days,
                l.latest_ts,
                l.latest_price,
                (
                    SELECT m.price_usd
                    FROM coingecko.market_data m
                    WHERE m.coingecko_id = l.coingecko_id
                      AND m.price_usd IS NOT NULL
                      AND m.ts <= l.latest_ts - make_interval(days => w.window_days)
                    ORDER BY m.ts DESC
                    LIMIT 1
                ) AS lookback_price,
                (
                    SELECT m.ts
                    FROM coingecko.market_data m
                    WHERE m.coingecko_id = l.coingecko_id
                      AND m.price_usd IS NOT NULL
                      AND m.ts <= l.latest_ts - make_interval(days => w.window_days)
                    ORDER BY m.ts DESC
                    LIMIT 1
                ) AS lookback_ts
            FROM latest l
            CROSS JOIN windows w
        ),
        returns AS (
            SELECT
                coingecko_id,
                window_days,
                latest_ts,
                latest_price,
                lookback_ts,
                lookback_price,
                CASE
                    WHEN lookback_price IS NULL OR lookback_price = 0 THEN NULL
                    ELSE 100.0 * (latest_price - lookback_price) / lookback_price
                END AS return_pct
            FROM lookback
        )
        SELECT
            a.coingecko_id     AS asset,
            p.coingecko_id     AS peer,
            a.window_days      AS window_days,
            a.latest_ts        AS asset_latest_ts,
            a.lookback_ts      AS asset_lookback_ts,
            a.latest_price     AS asset_latest_price,
            a.lookback_price   AS asset_lookback_price,
            a.return_pct       AS asset_return_pct,
            p.latest_ts        AS peer_latest_ts,
            p.lookback_ts      AS peer_lookback_ts,
            p.latest_price     AS peer_latest_price,
            p.lookback_price   AS peer_lookback_price,
            p.return_pct       AS peer_return_pct,
            CASE
                WHEN a.return_pct IS NULL OR p.return_pct IS NULL THEN NULL
                ELSE a.return_pct - p.return_pct
            END                AS relative_strength_pct
        FROM returns a
        JOIN returns p ON p.window_days = a.window_days
        WHERE a.coingecko_id != p.coingecko_id
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS analytics.crypto_relative_strength")
    # Leave the analytics schema in place — other future views may live there.
