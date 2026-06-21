"""Recreate analytics.macro_regime_per_date with the horizon column (B-096).

Latent bug: the 20260522 migration's view definition was later edited to add
``'macro:cross-sleeve:primary'::text AS horizon`` (and ``macro_regime.load_regimes``
+ the ``genkei macro-regime`` CLI were updated to read it), but because Alembic
had already applied that revision, the ``CREATE OR REPLACE VIEW`` never re-ran —
the deployed view still lacked ``horizon``, so the loader + CLI errored with
``column "horizon" does not exist``. The macro-regime emitter (B-096) reads the
same loader, so it surfaced the break.

``horizon`` sits mid-column (after ``regime``), so ``CREATE OR REPLACE VIEW``
can't add it — Postgres only appends columns. Drop + recreate with the
definition that matches the 20260522 file's current (intended) form.

Revision ID: d5e6f7088990
Revises: c4d5e6f77889
Create Date: 2026-06-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d5e6f7088990"
down_revision: str | Sequence[str] | None = "c4d5e6f77889"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_VIEW_SQL = """
CREATE VIEW analytics.macro_regime_per_date AS
WITH dgs10_calendar AS (
    SELECT DISTINCT ON (ts)
        ts::date AS ts,
        value    AS dgs10
    FROM fred.observations
    WHERE series_id = 'DGS10'
      AND ts >= '2006-01-01'
      AND value IS NOT NULL
    ORDER BY ts, realtime_start DESC
),
joined AS (
    SELECT
        c.ts,
        c.dgs10,
        hy_now.value      AS hy_oas,
        vix_now.value     AS vix,
        usd_now.value     AS usd_index,
        dgs10_30d.value   AS dgs10_30d_ago,
        hy_30d.value      AS hy_oas_30d_ago,
        usd_30d.value     AS usd_index_30d_ago
    FROM dgs10_calendar c
    LEFT JOIN LATERAL (
        SELECT value FROM fred.observations
        WHERE series_id = 'BAMLH0A0HYM2' AND ts <= c.ts AND value IS NOT NULL
        ORDER BY ts DESC, realtime_start DESC LIMIT 1
    ) hy_now ON true
    LEFT JOIN LATERAL (
        SELECT value FROM fred.observations
        WHERE series_id = 'VIXCLS' AND ts <= c.ts AND value IS NOT NULL
        ORDER BY ts DESC, realtime_start DESC LIMIT 1
    ) vix_now ON true
    LEFT JOIN LATERAL (
        SELECT value FROM fred.observations
        WHERE series_id = 'DTWEXBGS' AND ts <= c.ts AND value IS NOT NULL
        ORDER BY ts DESC, realtime_start DESC LIMIT 1
    ) usd_now ON true
    LEFT JOIN LATERAL (
        SELECT value FROM fred.observations
        WHERE series_id = 'DGS10' AND ts <= c.ts - INTERVAL '30 days' AND value IS NOT NULL
        ORDER BY ts DESC, realtime_start DESC LIMIT 1
    ) dgs10_30d ON true
    LEFT JOIN LATERAL (
        SELECT value FROM fred.observations
        WHERE series_id = 'BAMLH0A0HYM2' AND ts <= c.ts - INTERVAL '30 days' AND value IS NOT NULL
        ORDER BY ts DESC, realtime_start DESC LIMIT 1
    ) hy_30d ON true
    LEFT JOIN LATERAL (
        SELECT value FROM fred.observations
        WHERE series_id = 'DTWEXBGS' AND ts <= c.ts - INTERVAL '30 days' AND value IS NOT NULL
        ORDER BY ts DESC, realtime_start DESC LIMIT 1
    ) usd_30d ON true
),
features AS (
    SELECT
        ts, dgs10, hy_oas, vix, usd_index,
        (dgs10 - dgs10_30d_ago)::numeric         AS dgs10_30d_change,
        (hy_oas - hy_oas_30d_ago)::numeric       AS hy_oas_30d_change,
        (usd_index - usd_index_30d_ago)::numeric AS usd_index_30d_change,
        ((dgs10 IS NOT NULL)::int
         + (hy_oas IS NOT NULL)::int
         + (vix IS NOT NULL)::int
         + (usd_index IS NOT NULL)::int) AS available_inputs
    FROM joined
)
SELECT
    ts,
    CASE
        WHEN available_inputs < 3 THEN 'mixed'
        WHEN dgs10_30d_change > 0.3
             AND hy_oas_30d_change > 0.3
             AND vix > 25 THEN 'tightening_stress'
        WHEN hy_oas > 5.0 OR vix > 25 THEN 'risk_off'
        WHEN dgs10_30d_change < -0.5 THEN 'easing'
        WHEN (COALESCE((hy_oas < 3.5)::int, 0)
              + COALESCE((vix < 18)::int, 0)
              + COALESCE((usd_index_30d_change < -1)::int, 0)
              + COALESCE((dgs10_30d_change < -0.3)::int, 0)) >= 2 THEN 'risk_on'
        ELSE 'mixed'
    END                              AS regime,
    'macro:cross-sleeve:primary'::text AS horizon,
    dgs10,
    dgs10_30d_change,
    hy_oas,
    hy_oas_30d_change,
    vix,
    usd_index,
    usd_index_30d_change,
    available_inputs
FROM features
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS analytics.macro_regime_per_date")
    op.execute(_VIEW_SQL)


def downgrade() -> None:
    # Revert to the horizon-less shape the deployed view had before this fix.
    op.execute("DROP VIEW IF EXISTS analytics.macro_regime_per_date")
    op.execute(_VIEW_SQL.replace("'macro:cross-sleeve:primary'::text AS horizon,\n", ""))
