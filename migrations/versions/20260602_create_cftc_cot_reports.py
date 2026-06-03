"""Create cftc schema + cftc.cot_reports table (B-031).

The CFTC Commitments of Traders ingester lands one row per
``(report_date, market_code, trader_category)`` — the canonical
weekly-positioning fact for a CFTC-regulated futures market.

Shape:
  - ``cftc.cot_reports``  Plain table (not hypertable). Volume estimate:
                          ~10 markets × ~5 trader categories × 52 weeks
                          × 30 years ≈ 80k rows steady-state — well
                          within plain-PG range, same call B-093
                          (sec.form13f_holdings) and the meta.signal_events
                          table made.

Why ``cftc`` not ``futures``. Source-named schema follows the convention
from ``coinbase``, ``coingecko``, ``fred``, ``sec``, ``defillama``,
``yahoo``. The future B-104 CME daily-OI ingester goes in its own
``cme`` schema — the two sources are orthogonal (CFTC publishes
position breakdowns weekly; CME publishes settlement OI / volume
daily) and querying them shouldn't tangle table names.

Trader categories. The CFTC publishes three overlapping report
formats — categories differ per format, and we store them all in one
text column rather than three booleans / three tables:

  * **TFF** (Traders in Financial Futures) — covers financial futures
    (BTC, ETH, ES, NQ, FX, rates). Categories:
    ``dealer_intermediary``, ``asset_manager``, ``leveraged_funds``,
    ``other_reportables``, ``non_reportable``.
  * **Disaggregated** — covers commodities (gold, oil, ag, livestock).
    Categories: ``producer_merchant``, ``swap_dealer``,
    ``managed_money``, ``other_reportables``, ``non_reportable``.
  * **Legacy** — older format, simpler. Categories:
    ``non_commercial``, ``commercial``, ``non_reportable``.

The ingester writes whichever category set the upstream endpoint
publishes; queries filter on ``report_type`` when the distinction
matters (e.g. ``WHERE report_type = 'tff' AND trader_category =
'leveraged_funds'`` to get hedge-fund BTC futures positioning).

Why ``BIGINT`` not ``NUMERIC`` for positions. CFTC publishes
position counts as whole contracts (uint64 in practice) — no decimal
component to preserve. ``BIGINT`` is the right type and 4 bytes
narrower per row than ``NUMERIC``.

Revision ID: c9a4e7b21f08
Revises: d8e1f2a3b405
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c9a4e7b21f08"
down_revision: str | Sequence[str] | None = "d8e1f2a3b405"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS cftc")

    op.execute(
        """
        CREATE TABLE cftc.cot_reports (
            report_date         DATE        NOT NULL,
            market_code         TEXT        NOT NULL,
            market_name         TEXT        NOT NULL,
            report_type         TEXT        NOT NULL,
            trader_category     TEXT        NOT NULL,
            long_positions      BIGINT,
            short_positions     BIGINT,
            spreading_positions BIGINT,
            source_endpoint     TEXT        NOT NULL,
            fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id       BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (report_date, market_code, trader_category),
            CHECK (report_type IN ('tff', 'disaggregated', 'legacy'))
        )
        """
    )
    op.execute(
        "CREATE INDEX cot_reports_market_date_idx "
        "ON cftc.cot_reports (market_code, report_date DESC)"
    )
    op.execute(
        "CREATE INDEX cot_reports_category_date_idx "
        "ON cftc.cot_reports (trader_category, report_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cftc.cot_reports")
    op.execute("DROP SCHEMA IF EXISTS cftc")
