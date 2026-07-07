"""Create zcash schema + zcash.shielded_pools table (ZEC usage ingester).

Closes the load-bearing data gap from the 2026-07-06 ZEC research decision:
the lake had ZEC price but no *usage* signal, so the privacy-adoption thesis
was unmeasurable. The Zcash node's ``getblockchaininfo.valuePools`` (surfaced
free/keyless by ``zcashexplorer.app/api/v1/blockchain-info`` — see
``docs/sources/zcash-usage.md``) reports the on-chain ``chainValue`` held in
each pool: transparent (not private) vs the shielded pools (sprout / sapling /
orchard) vs the dev-fund lockbox. The headline thesis metric is the
**shielded share of supply** and, above all, its **trend** — is privacy
actually being adopted, or is the 11x price move pure narrative?

Shape: **long format**, one row per ``(pool, snapshot_date)``. Extensible — a
future consensus upgrade that adds a pool (as NU5 added Orchard, NU6 the
lockbox) needs no schema change. The shielded-share aggregate is derived at
query time (``sum(chain_value) FILTER (WHERE shielded) / sum(chain_value)``),
mirroring the ETF net-flow "derive at read time" convention (B-107/B-113).

``shielded`` is the privacy classification stored per row: true for sprout /
sapling / orchard; false for transparent and lockbox (the lockbox holds
deferred dev-fund ZEC — not user privacy, not freely-circulating transparent).

Forward-only: the source exposes only the current snapshot, so the series is a
daily-forward snapshot (like the iShares/Bitwise ETF-NAV ingesters) — deep
history would require a full Zcash node. Volume estimate: ~5 pools × 365
days/year ≈ 1.8k rows/year — tiny.

Revision ID: c7f2a9b41d38
Revises: d5e6f7088990
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c7f2a9b41d38"
down_revision: str | Sequence[str] | None = "d5e6f7088990"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the zcash schema, shielded_pools table, and lookup index."""
    op.execute("CREATE SCHEMA IF NOT EXISTS zcash")

    op.execute(
        """
        CREATE TABLE zcash.shielded_pools (
            pool             TEXT           NOT NULL,
            snapshot_date    DATE           NOT NULL,
            chain_value_zec  NUMERIC(20, 8) NOT NULL,
            shielded         BOOLEAN        NOT NULL,
            block_height     BIGINT,
            source_endpoint  TEXT           NOT NULL,
            fetched_at       TIMESTAMPTZ    NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT         NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (pool, snapshot_date),
            CHECK (chain_value_zec >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX shielded_pools_date_idx "
        "ON zcash.shielded_pools (snapshot_date DESC)"
    )


def downgrade() -> None:
    """Drop the zcash shielded-pools table and schema."""
    op.execute("DROP TABLE IF EXISTS zcash.shielded_pools")
    op.execute("DROP SCHEMA IF EXISTS zcash")
