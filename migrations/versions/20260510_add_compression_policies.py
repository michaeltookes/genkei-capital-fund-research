"""Enable TimescaleDB native compression on every defillama hypertable.

Backfill (B-019) blows up time-series row counts roughly 100x; D-010
committed to compression as Phase 1 hygiene rather than a Phase 7
afterthought. Compression policies trigger on chunks older than 30 days
— recent data stays uncompressed for fast inserts/upserts, historical
data compresses to ~10x smaller.

`segmentby` is the column we filter on most for each table; ordering by
ts DESC matches the typical "give me the recent N days" query shape.
Both choices can be tuned later via `alter_table_set_options(...)`
without re-compressing existing chunks.

Revision ID: ef4af7ae37bb
Revises: c5b69bc02dbb
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "ef4af7ae37bb"
down_revision: str | Sequence[str] | None = "c5b69bc02dbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (hypertable, segmentby column, orderby clause) per table.
HYPERTABLES = [
    ("defillama.chain_tvl", "chain", "ts DESC"),
    ("defillama.stablecoins", "asset_id", "ts DESC"),
    ("defillama.prices", "asset_key", "ts DESC"),
    ("defillama.protocol_tvl", "slug", "ts DESC"),
]
COMPRESS_AFTER = "INTERVAL '30 days'"


def upgrade() -> None:
    for table, segmentby, orderby in HYPERTABLES:
        op.execute(
            f"""
            ALTER TABLE {table} SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = '{segmentby}',
                timescaledb.compress_orderby = '{orderby}'
            )
            """
        )
        op.execute(
            f"SELECT add_compression_policy('{table}', {COMPRESS_AFTER}, if_not_exists => TRUE)"
        )


def downgrade() -> None:
    for table, _segmentby, _orderby in HYPERTABLES:
        op.execute(f"SELECT remove_compression_policy('{table}', if_exists => TRUE)")
        op.execute(
            f"""
            ALTER TABLE {table} SET (
                timescaledb.compress = false
            )
            """
        )
