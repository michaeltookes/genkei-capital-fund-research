"""Create meta.anomalies — per-series statistical-outlier flags (B-069).

The anomaly detector (``genkei.experiments.anomaly_detection`` +
``anomaly_emitter``) walks a numeric series per watchlist asset, computes a
rolling robust outlier score, and lands one row here for each observation
that breaches the threshold. Surfaced via ``genkei anomalies``.

Schema design choices:

  * **PK ``(asset, metric, ts, method)``** — natural key. ``asset`` is a
    ticker (equities) or a coingecko_id (crypto), matching the identifier
    convention the other signal tables use. ``metric`` names *what* series
    was scanned (``daily_return`` in v1; the column is deliberately open so
    a TVL-level or macro-level metric can land alongside without a
    migration). ``method`` records which statistic fired
    (``modified_zscore`` — MAD-based — or the ``zscore`` fallback when a
    flat window degenerates MAD to zero), so both can coexist for the same
    observation rather than clobbering.
  * **``score`` is signed** — positive = above the rolling median
    (``spike_up``), negative = below (``spike_down``); ``direction`` stores
    the label redundantly for cheap filtering.
  * **``median`` / ``mad`` nullable** — provenance of the rolling window
    that judged the point; nullable so the ``zscore`` fallback (which has no
    MAD) still lands.
  * **Plain table, not a hypertable.** Volume is tiny — only *flagged*
    observations land (a handful per asset per year by construction), so a
    hypertable's chunking overhead never pays.
  * **``ingest_run_id`` FK to meta.ingest_runs** — every row carries its
    computing run's provenance, same as meta.signals / meta.signal_events.

Revision ID: e8a1c2d34f5b
Revises: c7f2a9b41d38
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e8a1c2d34f5b"
down_revision: str | Sequence[str] | None = "c7f2a9b41d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meta.anomalies (
            asset          TEXT        NOT NULL,
            asset_class    TEXT        NOT NULL CHECK (asset_class IN ('equity', 'crypto')),
            metric         TEXT        NOT NULL,
            ts             TIMESTAMPTZ NOT NULL,
            value          NUMERIC     NOT NULL,
            score          NUMERIC     NOT NULL,
            method         TEXT        NOT NULL CHECK (method IN ('modified_zscore', 'zscore')),
            direction      TEXT        NOT NULL CHECK (direction IN ('spike_up', 'spike_down')),
            window_days    INTEGER     NOT NULL CHECK (window_days > 0),
            threshold      NUMERIC     NOT NULL CHECK (threshold > 0),
            median         NUMERIC,
            mad            NUMERIC,
            ingest_run_id  BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (asset, metric, ts, method)
        )
        """
    )
    # `(ts DESC)` for the default "what fired most recently" read.
    op.execute("CREATE INDEX anomalies_ts_idx ON meta.anomalies (ts DESC)")
    # `(asset, ts DESC)` for the per-asset drilldown.
    op.execute(
        "CREATE INDEX anomalies_asset_ts_idx ON meta.anomalies (asset, ts DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meta.anomalies")
