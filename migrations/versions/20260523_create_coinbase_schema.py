"""Create coinbase schema + coinbase.candles hypertable (B-035).

First Coinbase ingester migration. Adds a third crypto price source
alongside DeFiLlama and CoinGecko, primarily to break the 365-day
ceiling on CoinGecko's Demo/Public tier — Coinbase Exchange candles
go back to product-listing date (BTC-USD to 2015-07, ETH-USD to
2016-05) on a free, US-accessible, no-auth endpoint.

Shape:
  - coinbase.candles  Time-series fact, hypertable on ``ts`` (30-day
                      chunks, compression > 30 days). PK
                      ``(product, ts)``. One row per product per day
                      (granularity = 86400s daily candle) with the
                      Coinbase OHLCV shape: open / high / low / close /
                      volume_base (quoted in the base asset, e.g. BTC).

Why per-product (text) rather than per-coingecko-id (the CoinGecko
shape): the Coinbase product identifier is the natural exchange key
(BTC-USD, ETH-USD, …) and joining back to the watchlist resolves the
ticker. Doing it the CoinGecko way would force a fragile mapping
table that doesn't exist on the exchange side.

Pairs naturally with coingecko.market_data: same coin's price is
queryable from either source, useful for cross-exchange divergence
detection (which is part of B-035's "exchange-specific OHLCV
cross-checks" intent).

Revision ID: e4f1a2b3c901
Revises: c8e2f3a4d501
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e4f1a2b3c901"
down_revision: str | Sequence[str] | None = "c8e2f3a4d501"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS coinbase")

    op.execute(
        """
        CREATE TABLE coinbase.candles (
            product         TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            open            NUMERIC     NOT NULL,
            high            NUMERIC     NOT NULL,
            low             NUMERIC     NOT NULL,
            close           NUMERIC     NOT NULL,
            volume_base     NUMERIC     NOT NULL,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (product, ts)
        )
        """
    )
    op.execute("CREATE INDEX candles_ts_idx ON coinbase.candles (ts DESC)")
    op.execute("CREATE INDEX candles_product_ts_idx ON coinbase.candles (product, ts DESC)")

    op.execute(
        """
        SELECT create_hypertable(
            'coinbase.candles',
            'ts',
            chunk_time_interval => INTERVAL '30 days',
            if_not_exists => TRUE
        )
        """
    )

    op.execute(
        """
        ALTER TABLE coinbase.candles SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'product',
            timescaledb.compress_orderby = 'ts DESC'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('coinbase.candles', INTERVAL '30 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('coinbase.candles', if_exists => TRUE)")
    op.execute("DROP TABLE IF EXISTS coinbase.candles")
    op.execute("DROP SCHEMA IF EXISTS coinbase")
