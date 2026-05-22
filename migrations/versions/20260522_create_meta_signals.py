"""Create meta.signals — watchlist scoring rubric output (B-065).

Each daily scoring run lands one row per watchlist asset, recording the
composite score plus the per-component breakdown that produced it.
``rubric_version`` is part of the PK so future rubric iterations land
side-by-side rather than overwriting history — that's what makes the
scores themselves backtestable per CLAUDE.md's "data hygiene at
fund-grade" stance.

Schema design choices:

  * **PK `(asset, ts, rubric_version)`** — natural key. ``asset`` is a
    ticker for equities or a coingecko_id for crypto (the same shape
    `meta.signals` consumers use elsewhere). Multiple rubric versions
    can coexist for the same (asset, ts) so a new formula's output
    doesn't clobber the old.
  * **`components` JSONB** — per-component breakdown. Shape:
    ``{name: {score: numeric, detail: text, ...}}``. JSONB lets the
    rubric evolve (new components added) without a schema migration.
  * **Plain table, not hypertable.** Volume is ~35 watchlist assets ×
    1 score/day = ~13k rows/year. Hypertable overhead doesn't pay
    until orders of magnitude more.
  * **`ingest_run_id` FK to meta.ingest_runs** — every row carries the
    provenance trio (in spirit: the run's `metadata` JSONB captures the
    rubric inputs and the source data freshness at compute time).

Revision ID: 8c5d7e9f1a02
Revises: b1c2d3e4f5a6
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8c5d7e9f1a02"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meta.signals (
            asset           TEXT        NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            rubric_version  TEXT        NOT NULL,
            asset_class     TEXT        NOT NULL CHECK (asset_class IN ('equity', 'crypto')),
            sleeve          TEXT        NOT NULL,
            composite_score NUMERIC     NOT NULL,
            components      JSONB       NOT NULL,
            ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (asset, ts, rubric_version)
        )
        """
    )
    # `(ts DESC)` for "show me today's scores across the watchlist" reads.
    op.execute("CREATE INDEX signals_ts_idx ON meta.signals (ts DESC)")
    # `(rubric_version, ts DESC)` for version-scoped backtests once the
    # rubric has shipped multiple versions.
    op.execute(
        "CREATE INDEX signals_rubric_version_ts_idx "
        "ON meta.signals (rubric_version, ts DESC)"
    )
    # `(sleeve, ts DESC)` for sleeve-filtered reads (e.g. /research
    # sessions that only care about crypto-tactical scores).
    op.execute(
        "CREATE INDEX signals_sleeve_ts_idx ON meta.signals (sleeve, ts DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meta.signals")
