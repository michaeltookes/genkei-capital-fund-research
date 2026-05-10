"""Create defillama.protocol_tvl + hypertable.

Per-protocol per-chain TVL time-series, sibling to defillama.protocols
(slug-keyed entity dim). Backfill (B-019) walks /protocol/{slug} per
slug and lands the full per-chain history here.

PK is (slug, chain, ts) so we can re-derive `defillama.protocols.id`
indirection later if we ever need it; staying with slug for now keeps
the upsert path obvious. FK back to defillama.protocols(slug) so we
can't write a tvl row for a protocol the dim hasn't seen.

The hypertable conversion lives in this same file (single new table,
single migration) — the storage.md "hypertable in its own migration"
guidance is about not coupling the Timescale layer to *existing*
table DDL, not about gratuitously splitting greenfield tables.

Revision ID: c5b69bc02dbb
Revises: c4e180fcf605
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c5b69bc02dbb"
down_revision: str | Sequence[str] | None = "c4e180fcf605"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE defillama.protocol_tvl (
            slug            TEXT        NOT NULL
                REFERENCES defillama.protocols(slug),
            chain           TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            tvl_usd         NUMERIC,
            source_endpoint TEXT        NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (slug, chain, ts)
        )
        """
    )
    op.execute("CREATE INDEX protocol_tvl_ts_idx ON defillama.protocol_tvl (ts DESC)")
    op.execute("CREATE INDEX protocol_tvl_chain_ts_idx ON defillama.protocol_tvl (chain, ts DESC)")

    op.execute(
        """
        SELECT create_hypertable(
            'defillama.protocol_tvl',
            'ts',
            chunk_time_interval => INTERVAL '30 days',
            if_not_exists => TRUE
        )
        """
    )


def downgrade() -> None:
    # Per the same pattern as the existing defillama hypertables: hypertable
    # conversion is not reversible in place. The table drop here removes
    # both the table and its hypertable wrapper.
    op.execute("DROP TABLE IF EXISTS defillama.protocol_tvl")
