"""Create yahoo schema + yahoo.candles hypertable (B-092).

Equity counterpart to B-035's `coinbase.candles`. Yahoo Finance's
public chart endpoint serves daily OHLCV back to each ticker's
listing date (AAPL to 1980-12-12, ~45y history) with no auth, no
geo-block, and a single request per ticker that returns the full
range — no chunking required.

Shape:
  - yahoo.candles  Time-series fact, hypertable on ``ts`` (30-day
                   chunks, compression > 30 days). PK ``(ticker, ts)``.
                   Carries both unadjusted ``close`` (what showed on
                   the tape that day) and ``adj_close`` (Yahoo's split-
                   and-dividend-adjusted close — the right input for
                   return calculations).

**Why both close and adj_close.** Unadjusted close is what reported
volume * price multiplies into actual notional traded; adj_close is
the right thing to feed into return / drawdown / regression
calculations. Storing both lets queries pick the right one without
us baking an opinion into the table. ``adj_close`` is NULL only
when Yahoo doesn't publish an adjustment (rare — typically only for
very recent IPOs or delisted tickers).

**Why ``yahoo`` not ``equities``.** Source-named schema follows the
convention from `coinbase`, `coingecko`, `fred`, `sec`, `defillama`.
If a second equity source ever lands (Stooq, EOD Historical, etc.),
each goes in its own schema and the CLI dispatches via a
``--source`` flag (same pattern as B-035 / B-039 on the crypto
side).

Revision ID: f5b2c8d3e914
Revises: e4f1a2b3c901
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f5b2c8d3e914"
down_revision: str | Sequence[str] | None = "e4f1a2b3c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS yahoo")

    op.execute(
        """
        CREATE TABLE yahoo.candles (
            ticker          TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            open            NUMERIC     NOT NULL,
            high            NUMERIC     NOT NULL,
            low             NUMERIC     NOT NULL,
            close           NUMERIC     NOT NULL,
            adj_close       NUMERIC,
            volume          NUMERIC     NOT NULL,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (ticker, ts)
        )
        """
    )
    op.execute("CREATE INDEX yahoo_candles_ts_idx ON yahoo.candles (ts DESC)")
    op.execute(
        "CREATE INDEX yahoo_candles_ticker_ts_idx ON yahoo.candles (ticker, ts DESC)"
    )

    op.execute(
        """
        SELECT create_hypertable(
            'yahoo.candles',
            'ts',
            chunk_time_interval => INTERVAL '30 days',
            if_not_exists => TRUE
        )
        """
    )

    op.execute(
        """
        ALTER TABLE yahoo.candles SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'ticker',
            timescaledb.compress_orderby = 'ts DESC'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('yahoo.candles', INTERVAL '30 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('yahoo.candles', if_exists => TRUE)")
    op.execute("DROP TABLE IF EXISTS yahoo.candles")
    op.execute("DROP SCHEMA IF EXISTS yahoo")
