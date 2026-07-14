"""Create analytics.price_momentum — trailing 3/7/30d return matview (B-067).

Common-window momentum precomputed once per refresh rather than recomputed in
every CLI call (the B-067 motivation). One row per (asset, asset_class) at the
asset's latest close, carrying its trailing 3-day / 7-day / 30-day returns.

Sources (the two dense price series, matching the anomaly detector's scope):
  * crypto  — ``coinbase.candles`` (``close``), asset = the symbol left of the
              product's ``-USD`` (``BTC-USD`` → ``BTC``).
  * equity  — ``yahoo.candles`` (``adj_close`` — the split/dividend-adjusted
              close, the correct return input per the B-124 audit), asset =
              ticker.

Return math mirrors ``genkei.experiments.relative_strength.compute_return_pct``:
``(latest_px - lookback_px) / lookback_px * 100`` where ``lookback_px`` is the
most recent close **at or before** ``latest_ts - N days`` (calendar days, via a
correlated subquery on the ``(product/ticker, ts)`` index). NULL when the asset
lacks enough history for a window (e.g. a coin listed 10 days ago has no 30d
return) — NULL, never a fake zero, so the CLI reads "n/a" loud.

**MATERIALIZED, not live** (unlike ``analytics.crypto_relative_strength`` and
``analytics.macro_regime_per_date``, which are cheap live views). B-067 asks
for materialized views, and a matview is the idiomatic Postgres answer to
"don't recompute per call." The unique index below lets
``REFRESH MATERIALIZED VIEW CONCURRENTLY`` run without locking readers.

**Refresh cadence:** daily, by ``genkei.experiments.refresh_price_momentum``
(wrapped in a ``meta.ingest_runs`` row so ``genkei watchlist health`` catches a
stale matview — the failure mode a materialized view has that a live view does
not). Wired in ``.github/workflows/trend-views-daily.yml`` at 13:30 UTC, after
the Coinbase (12:00) and Yahoo (12:15) price crons land the day's candles.

Revision ID: f9b2c3d45e6a
Revises: e8a1c2d34f5b
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f9b2c3d45e6a"
down_revision: str | Sequence[str] | None = "e8a1c2d34f5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_VIEW_SQL = """
CREATE MATERIALIZED VIEW analytics.price_momentum AS
-- Crypto side: coinbase.candles, asset = symbol left of '-USD'.
SELECT
    split_part(l.product, '-', 1)      AS asset,
    'crypto'::text                     AS asset_class,
    l.latest_ts::date                  AS ts,
    l.latest_px                        AS close,
    CASE WHEN lb.px3  IS NULL OR lb.px3  = 0 THEN NULL
         ELSE (l.latest_px - lb.px3)  / lb.px3  * 100 END AS ret_3d,
    CASE WHEN lb.px7  IS NULL OR lb.px7  = 0 THEN NULL
         ELSE (l.latest_px - lb.px7)  / lb.px7  * 100 END AS ret_7d,
    CASE WHEN lb.px30 IS NULL OR lb.px30 = 0 THEN NULL
         ELSE (l.latest_px - lb.px30) / lb.px30 * 100 END AS ret_30d
FROM (
    SELECT DISTINCT ON (product) product, ts AS latest_ts, close AS latest_px
    FROM coinbase.candles
    WHERE close IS NOT NULL
    ORDER BY product, ts DESC
) l
CROSS JOIN LATERAL (
    SELECT
        (SELECT close FROM coinbase.candles c
         WHERE c.product = l.product AND c.close IS NOT NULL
           AND c.ts <= l.latest_ts - INTERVAL '3 days'
         ORDER BY c.ts DESC LIMIT 1) AS px3,
        (SELECT close FROM coinbase.candles c
         WHERE c.product = l.product AND c.close IS NOT NULL
           AND c.ts <= l.latest_ts - INTERVAL '7 days'
         ORDER BY c.ts DESC LIMIT 1) AS px7,
        (SELECT close FROM coinbase.candles c
         WHERE c.product = l.product AND c.close IS NOT NULL
           AND c.ts <= l.latest_ts - INTERVAL '30 days'
         ORDER BY c.ts DESC LIMIT 1) AS px30
) lb

UNION ALL

-- Equity side: yahoo.candles, adj_close (split/dividend adjusted), asset = ticker.
SELECT
    l.ticker                           AS asset,
    'equity'::text                     AS asset_class,
    l.latest_ts::date                  AS ts,
    l.latest_px                        AS close,
    CASE WHEN lb.px3  IS NULL OR lb.px3  = 0 THEN NULL
         ELSE (l.latest_px - lb.px3)  / lb.px3  * 100 END AS ret_3d,
    CASE WHEN lb.px7  IS NULL OR lb.px7  = 0 THEN NULL
         ELSE (l.latest_px - lb.px7)  / lb.px7  * 100 END AS ret_7d,
    CASE WHEN lb.px30 IS NULL OR lb.px30 = 0 THEN NULL
         ELSE (l.latest_px - lb.px30) / lb.px30 * 100 END AS ret_30d
FROM (
    SELECT DISTINCT ON (ticker) ticker, ts AS latest_ts, adj_close AS latest_px
    FROM yahoo.candles
    WHERE adj_close IS NOT NULL
    ORDER BY ticker, ts DESC
) l
CROSS JOIN LATERAL (
    SELECT
        (SELECT adj_close FROM yahoo.candles y
         WHERE y.ticker = l.ticker AND y.adj_close IS NOT NULL
           AND y.ts <= l.latest_ts - INTERVAL '3 days'
         ORDER BY y.ts DESC LIMIT 1) AS px3,
        (SELECT adj_close FROM yahoo.candles y
         WHERE y.ticker = l.ticker AND y.adj_close IS NOT NULL
           AND y.ts <= l.latest_ts - INTERVAL '7 days'
         ORDER BY y.ts DESC LIMIT 1) AS px7,
        (SELECT adj_close FROM yahoo.candles y
         WHERE y.ticker = l.ticker AND y.adj_close IS NOT NULL
           AND y.ts <= l.latest_ts - INTERVAL '30 days'
         ORDER BY y.ts DESC LIMIT 1) AS px30
) lb
"""


def upgrade() -> None:
    op.execute(_VIEW_SQL)
    # Unique key (one row per asset per class) — REQUIRED for
    # REFRESH MATERIALIZED VIEW CONCURRENTLY.
    op.execute(
        "CREATE UNIQUE INDEX price_momentum_asset_idx "
        "ON analytics.price_momentum (asset_class, asset)"
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS analytics.price_momentum")
