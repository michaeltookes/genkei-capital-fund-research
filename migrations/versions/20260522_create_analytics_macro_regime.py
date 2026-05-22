"""Create analytics.macro_regime_per_date view (B-059).

Phase 5 experiment that buckets every business day into one of five
macro regime labels (``risk_on`` / ``risk_off`` / ``easing`` /
``tightening_stress`` / ``mixed``) derived from FRED daily series.

Lives in the ``analytics`` schema following the
``analytics.crypto_relative_strength`` precedent (B-090) — a derived
view that joins across raw-source rows but emits a single signal-row
shape. **Live view, not materialized:** the underlying math is cheap
(four ``DISTINCT ON`` lookups per row of the DGS10 calendar; DGS10 has
~33k rows so the full view materializes in well under a second).

Schema:

  ts                   DATE
  regime               TEXT       -- one of {risk_on, risk_off, easing,
                                  --         tightening_stress, mixed}
  dgs10                NUMERIC    -- 10y Treasury yield (%)
  dgs10_30d_change     NUMERIC    -- DGS10 - DGS10@30d_ago (pp)
  hy_oas               NUMERIC    -- BAMLH0A0HYM2 (%)
  hy_oas_30d_change    NUMERIC    -- HY OAS - HY OAS@30d_ago (pp)
  vix                  NUMERIC    -- VIXCLS
  usd_index            NUMERIC    -- DTWEXBGS
  usd_index_30d_change NUMERIC    -- USD - USD@30d_ago
  available_inputs     INTEGER    -- 0..4, how many of the four inputs
                                  -- had a value on this date

Coverage:

  Anchored on DGS10 dates (1962-present, daily). The other inputs come
  in later (BAMLH0A0HYM2 from 2023, DTWEXBGS from 2006, VIXCLS from
  1990) — pre-1990 rows have only DGS10 and ``available_inputs = 1``;
  the regime label degrades gracefully to ``mixed`` whenever fewer
  than 3 inputs are available.

Regime priority (mutually exclusive):

  1. tightening_stress — DGS10 rising AND HY widening AND VIX elevated
  2. risk_off          — HY wide OR VIX elevated (regardless of rates)
  3. easing            — DGS10 falling > 0.5pp over 30d
  4. risk_on           — composite score ≥ 2 from low-VIX, low-HY,
                         weakening-USD, falling-DGS10 inputs
  5. mixed             — default

The Python equivalent in ``genkei.experiments.macro_regime`` runs the
same rule with the same thresholds — both are unit-tested and a
sample-row cross-check guards against drift between the SQL and
Python implementations.

Revision ID: c8e2f3a4d501
Revises: 8c5d7e9f1a02
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c8e2f3a4d501"
down_revision: str | Sequence[str] | None = "8c5d7e9f1a02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")

    op.execute(
        """
        CREATE OR REPLACE VIEW analytics.macro_regime_per_date AS
        WITH dgs10_calendar AS (
            -- DGS10 has the longest daily coverage (1962+) but pre-2006
            -- we have at most 2 of 4 inputs (no USD index, no HY OAS)
            -- so the regime label degrades to 'mixed'. Restricting the
            -- calendar to 2006+ trims ~26k rows from the view at zero
            -- information loss. Pre-2006 macro context can be added in
            -- a follow-up via backfilled USD-substitute series.
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
                -- Forward-fill each input by querying fred.observations
                -- directly (uses the (series_id, ts DESC) index).
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
                -- Need at least 3 of 4 inputs to make any non-trivial call.
                WHEN available_inputs < 3 THEN 'mixed'
                -- Priority 1: tightening_stress — the worst regime,
                -- rates up + credit widening + vol elevated.
                WHEN dgs10_30d_change > 0.3
                     AND hy_oas_30d_change > 0.3
                     AND vix > 25 THEN 'tightening_stress'
                -- Priority 2: risk_off — any one of HY wide or VIX
                -- elevated triggers, regardless of rate direction.
                WHEN hy_oas > 5.0 OR vix > 25 THEN 'risk_off'
                -- Priority 3: easing — significant rate drop, often
                -- the start of a recovery / Fed pivot regime.
                WHEN dgs10_30d_change < -0.5 THEN 'easing'
                -- Priority 4: risk_on — composite of bull-leaning inputs.
                -- +1 for each bullish input present, then ≥ 2 to fire.
                WHEN ((hy_oas < 3.5)::int
                      + (vix < 18)::int
                      + (usd_index_30d_change < -1)::int
                      + (dgs10_30d_change < -0.3)::int) >= 2 THEN 'risk_on'
                ELSE 'mixed'
            END                              AS regime,
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
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS analytics.macro_regime_per_date")
