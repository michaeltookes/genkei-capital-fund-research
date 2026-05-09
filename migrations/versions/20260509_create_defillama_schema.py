"""Create defillama schema and four core tables.

First DeFiLlama schema migration (B-016). Creates the per-source `defillama`
schema and four tables that map to the public DeFiLlama endpoints the
existing collector already pulls:

  - defillama.protocols    -- entity dimension (slug-keyed, slowly changing)
  - defillama.chain_tvl    -- time-series fact, PK (chain, ts)
  - defillama.stablecoins  -- time-series fact, PK (asset_id, chain, ts)
  - defillama.prices       -- time-series fact, PK (asset_key, ts)

Hypertable conversion lives in a separate migration so the Timescale layer
can be peeled back without dropping the tables (per docs/storage.md).

Revision ID: ff3f33d2105d
Revises: 69f3fe427252
Create Date: 2026-05-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "ff3f33d2105d"
down_revision: str | Sequence[str] | None = "69f3fe427252"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS defillama")

    op.execute(
        """
        CREATE TABLE defillama.protocols (
            id              BIGSERIAL PRIMARY KEY,
            slug            TEXT        NOT NULL UNIQUE,
            defillama_id    TEXT,
            name            TEXT        NOT NULL,
            category        TEXT,
            chains          TEXT[],
            url             TEXT,
            description     TEXT,
            parent_protocol TEXT,
            twitter         TEXT,
            first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )
    op.execute("CREATE INDEX protocols_category_idx ON defillama.protocols (category)")
    op.execute("CREATE INDEX protocols_chains_gin ON defillama.protocols USING GIN (chains)")

    op.execute(
        """
        CREATE TABLE defillama.chain_tvl (
            chain           TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            tvl_usd         NUMERIC,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (chain, ts)
        )
        """
    )
    op.execute("CREATE INDEX chain_tvl_ts_idx ON defillama.chain_tvl (ts DESC)")

    op.execute(
        """
        CREATE TABLE defillama.stablecoins (
            asset_id        TEXT        NOT NULL,
            chain           TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            symbol          TEXT        NOT NULL,
            name            TEXT,
            peg_type        TEXT,
            supply_usd      NUMERIC,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (asset_id, chain, ts)
        )
        """
    )
    op.execute("CREATE INDEX stablecoins_ts_idx ON defillama.stablecoins (ts DESC)")
    op.execute("CREATE INDEX stablecoins_chain_ts_idx ON defillama.stablecoins (chain, ts DESC)")
    op.execute("CREATE INDEX stablecoins_symbol_ts_idx ON defillama.stablecoins (symbol, ts DESC)")

    op.execute(
        """
        CREATE TABLE defillama.prices (
            asset_key       TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            price_usd       NUMERIC     NOT NULL,
            confidence      DOUBLE PRECISION,
            symbol          TEXT,
            decimals        INTEGER,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (asset_key, ts)
        )
        """
    )
    op.execute("CREATE INDEX prices_ts_idx ON defillama.prices (ts DESC)")
    op.execute("CREATE INDEX prices_symbol_ts_idx ON defillama.prices (symbol, ts DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS defillama.prices")
    op.execute("DROP TABLE IF EXISTS defillama.stablecoins")
    op.execute("DROP TABLE IF EXISTS defillama.chain_tvl")
    op.execute("DROP TABLE IF EXISTS defillama.protocols")
    op.execute("DROP SCHEMA IF EXISTS defillama")
