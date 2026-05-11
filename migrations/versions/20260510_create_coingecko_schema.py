"""Create coingecko schema, coingecko.coins, coingecko.market_data.

First CoinGecko schema migration (B-034). Adds a second crypto price
source alongside DeFiLlama, primarily for cross-checking
(``defillama.prices`` already covers BTC/ETH/SOL/LINK/SUI) plus
market_cap / 24h volume which DeFiLlama doesn't expose.

Tables:
  - coingecko.coins        Entity dim. PK ``coingecko_id`` (string like
                           "bitcoin"). Holds symbol/name/market_cap_rank/
                           genesis_date metadata from ``/coins/{id}``.
  - coingecko.market_data  Time-series fact, hypertable on ``ts``
                           (30-day chunks, compression > 30 days). PK
                           ``(coingecko_id, ts)``. One row per coin per
                           day with price_usd / market_cap_usd /
                           volume_usd from the market_chart endpoint.

Revision ID: 05c48dd08fb0
Revises: 0f3acd7fbf46
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "05c48dd08fb0"
down_revision: str | Sequence[str] | None = "0f3acd7fbf46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS coingecko")

    op.execute(
        """
        CREATE TABLE coingecko.coins (
            coingecko_id     TEXT        PRIMARY KEY,
            symbol           TEXT,
            name             TEXT,
            market_cap_rank  INTEGER,
            genesis_date     DATE,
            description      TEXT,
            homepage         TEXT,
            categories       TEXT[],
            source_endpoint  TEXT        NOT NULL,
            fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )
    op.execute("CREATE INDEX coins_symbol_idx ON coingecko.coins (symbol)")

    op.execute(
        """
        CREATE TABLE coingecko.market_data (
            coingecko_id    TEXT        NOT NULL REFERENCES coingecko.coins(coingecko_id),
            ts              TIMESTAMPTZ NOT NULL,
            price_usd       NUMERIC,
            market_cap_usd  NUMERIC,
            volume_usd      NUMERIC,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (coingecko_id, ts)
        )
        """
    )
    op.execute("CREATE INDEX market_data_ts_idx ON coingecko.market_data (ts DESC)")

    op.execute(
        """
        SELECT create_hypertable(
            'coingecko.market_data',
            'ts',
            chunk_time_interval => INTERVAL '30 days',
            if_not_exists => TRUE
        )
        """
    )

    op.execute(
        """
        ALTER TABLE coingecko.market_data SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'coingecko_id',
            timescaledb.compress_orderby = 'ts DESC'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('coingecko.market_data', INTERVAL '30 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('coingecko.market_data', if_exists => TRUE)")
    op.execute("DROP TABLE IF EXISTS coingecko.market_data")
    op.execute("DROP TABLE IF EXISTS coingecko.coins")
    op.execute("DROP SCHEMA IF EXISTS coingecko")
