"""Create defillama.protocol_fees (B-083).

Per-protocol daily fee + revenue series, sibling to defillama.protocol_tvl.
Sourced from DefiLlama's /summary/fees/{slug} (universal) +
/summary/revenue/{slug} (often missing — many protocols only report fees,
not separately a revenue cut).

Schema mirrors protocol_tvl: slug-keyed FK to defillama.protocols, daily
timestamp PK, NUMERIC value columns. Hypertable on ts with 30-day chunks
+ compression policy >30d old, matching protocol_tvl.

Two value columns instead of one because revenue is a *subset* of fees
in DefiLlama's model — fees = total paid by users; revenue = the
portion captured by the protocol vs passed through to LPs/node-operators.
For oracles like Chainlink, fees pass through to node operators so
revenue is undefined and DefiLlama returns 500 on the revenue endpoint;
the column stays NULL in that case. The single (slug, ts) row carries
both values when both are known, so queries don't need to JOIN.

Revision ID: 7e4d2a1f8b35
Revises: 5d3e8b9c1a02
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7e4d2a1f8b35"
down_revision: str | Sequence[str] | None = "5d3e8b9c1a02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE defillama.protocol_fees (
            slug            TEXT        NOT NULL
                REFERENCES defillama.protocols(slug),
            ts              TIMESTAMPTZ NOT NULL,
            fees_usd        NUMERIC,
            revenue_usd     NUMERIC,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (slug, ts)
        )
        """
    )
    op.execute("CREATE INDEX protocol_fees_ts_idx ON defillama.protocol_fees (ts DESC)")

    op.execute(
        """
        SELECT create_hypertable(
            'defillama.protocol_fees',
            'ts',
            chunk_time_interval => INTERVAL '30 days',
            if_not_exists => TRUE
        )
        """
    )
    op.execute(
        """
        ALTER TABLE defillama.protocol_fees SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'slug',
            timescaledb.compress_orderby = 'ts DESC'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('defillama.protocol_fees', "
        "INTERVAL '30 days', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.execute(
        "SELECT remove_compression_policy('defillama.protocol_fees', if_exists => TRUE)"
    )
    op.execute("DROP TABLE IF EXISTS defillama.protocol_fees")
