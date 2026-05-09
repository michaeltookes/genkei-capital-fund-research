"""Convert DeFiLlama time-series tables to TimescaleDB hypertables.

Separates the Timescale-specific layer from the table DDL (per
docs/storage.md) so the hypertable conversion can be reverted without
dropping the underlying tables.

Hypertables created:
  - defillama.chain_tvl    chunk_time_interval => 30 days
  - defillama.stablecoins  chunk_time_interval => 30 days
  - defillama.prices       chunk_time_interval => 7 days  (intraday-eligible)

Chunk intervals are sized so a chunk fits comfortably in memory at
expected ingest volume; tunable later via `set_chunk_time_interval()`
without rewriting history.

Revision ID: 6d578bda9706
Revises: ff3f33d2105d
Create Date: 2026-05-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "6d578bda9706"
down_revision: str | Sequence[str] | None = "ff3f33d2105d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        SELECT create_hypertable(
            'defillama.chain_tvl',
            'ts',
            chunk_time_interval => INTERVAL '30 days',
            if_not_exists => TRUE
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
            'defillama.stablecoins',
            'ts',
            chunk_time_interval => INTERVAL '30 days',
            if_not_exists => TRUE
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
            'defillama.prices',
            'ts',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
        """
    )


def downgrade() -> None:
    # Hypertable conversion is not reversible in place; the only safe
    # downgrade for a single table is to copy rows to a plain table and
    # swap. Since the parent migration drops these tables on its own
    # downgrade, we raise here to make the intent explicit: roll back
    # the schema migration to remove hypertables, don't try to peel off
    # the Timescale layer alone.
    raise NotImplementedError(
        "Hypertables cannot be reverted in place. Downgrade the parent "
        "schema migration (ff3f33d2105d) to drop the underlying tables "
        "instead."
    )
