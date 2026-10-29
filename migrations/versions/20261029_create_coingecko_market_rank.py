"""Create coingecko.market_rank (B-149).

Daily top-N market-cap rank snapshot from CoinGecko ``/coins/markets``, kept
forever so the "top 15 is essentially chosen" thesis can be graded: one row per
coin per UTC day with rank and market cap. Forward-only series (the free tier
has no historical rank endpoint).

Deliberately a plain table, not a hypertable (~50 rows/day), and with no FK to
``coingecko.coins``: coins at ranks 16-50 are not watchlist coins and have no
metadata row.

Revision ID: a9c4e17d3b65
Revises: f8b3d59a02e4
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a9c4e17d3b65"
down_revision: str | Sequence[str] | None = "f8b3d59a02e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE coingecko.market_rank (
            ts                DATE        NOT NULL,
            coingecko_id      TEXT        NOT NULL,
            symbol            TEXT        NOT NULL,
            rank              SMALLINT    NOT NULL,
            market_cap_usd    NUMERIC,
            price_usd         NUMERIC,
            volume_usd        NUMERIC,
            fully_diluted_usd NUMERIC,
            source_endpoint   TEXT        NOT NULL,
            fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id     BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (ts, coingecko_id)
        )
        """
    )
    op.execute("CREATE INDEX market_rank_ts_rank_idx ON coingecko.market_rank (ts DESC, rank)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS coingecko.market_rank")
