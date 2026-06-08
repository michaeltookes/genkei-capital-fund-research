"""Create onchain.eth_whale_flows + aggregate view (B-106).

The B-106 whale-flow tracker lands one row per ``(address, ts)`` per
day from Etherscan v2 — balance + 24h net flow + tx count for each
curated whale address in ``config/watchlists.yml``. See
``docs/sources/eth-whale-addresses.md`` for the curation methodology
and the four hard limits callers must read alongside the data.

Schema choice: NEW table in the existing ``onchain`` schema, alongside
``staking_events`` (B-082 / B-086) and ``sui_validators`` /
``sui_unlocks`` (B-088 / B-089). Cross-source queries — e.g. "are
whales flowing into exchanges in the same week the ETF net flow turned
negative?" — don't need cross-schema joins.

Volume estimate: ~20 addresses × 365 days × 5 years = ~36k rows steady-
state. Plain table; no partitioning or hypertable conversion needed.

ETH amounts stored as ``NUMERIC(38, 18)``:
- 38 digits total covers any plausible ETH balance (the Beacon Deposit
  Contract is the single largest holder on the network at ~33M ETH =
  3.3 × 10^7 — far below the column's 10^20 ceiling).
- 18 fractional digits matches ETH's native wei precision so balance
  values round-trip without loss vs. the raw Etherscan response.

USD values stored as ``NUMERIC(20, 2)``:
- Cents precision is all anyone reads. Wider precision invites the
  illusion that the snapshot price is more accurate than it is — the
  USD value here is "the price at snapshot time", which is itself a
  lossy approximation of the actual day-mean price.

The aggregate VIEW (``onchain.eth_whale_flows_aggregate``) sums per
``(category, ts::date)`` so the headline "are whales net-selling?" query
is a one-liner. Computed on read so it always reflects the latest table
state — important when backfills add historical rows.

Revision ID: c8e9f0a1b224
Revises: b7f8e9d0c113
Create Date: 2026-06-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c8e9f0a1b224"
down_revision: str | Sequence[str] | None = "b7f8e9d0c113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the eth_whale_flows table + category aggregate view."""
    op.execute("CREATE SCHEMA IF NOT EXISTS onchain")

    op.execute(
        """
        CREATE TABLE onchain.eth_whale_flows (
            address                    TEXT           NOT NULL,
            ts                         TIMESTAMPTZ    NOT NULL,
            label                      TEXT           NOT NULL,
            category                   TEXT           NOT NULL,
            balance_eth                NUMERIC(38, 18) NOT NULL,
            balance_usd_at_snapshot    NUMERIC(20, 2),
            net_flow_eth_24h           NUMERIC(38, 18) NOT NULL DEFAULT 0,
            net_flow_usd_24h           NUMERIC(20, 2),
            tx_count_24h               INTEGER        NOT NULL DEFAULT 0,
            source_endpoint            TEXT           NOT NULL,
            fetched_at                 TIMESTAMPTZ    NOT NULL DEFAULT now(),
            ingest_run_id              BIGINT         NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (address, ts),
            CHECK (balance_eth >= 0),
            CHECK (tx_count_24h >= 0),
            CHECK (category IN ('exchange', 'custodian', 'foundation', 'whale'))
        )
        """
    )
    op.execute(
        "CREATE INDEX eth_whale_flows_ts_idx "
        "ON onchain.eth_whale_flows (ts DESC)"
    )
    op.execute(
        "CREATE INDEX eth_whale_flows_category_ts_idx "
        "ON onchain.eth_whale_flows (category, ts DESC)"
    )

    # Aggregate view — one row per (category, day) summing net flow + tx
    # count across every address in the category. The headline "are whales
    # net-selling?" question is a one-liner against this view.
    #
    # Critical interpretation note (mirrored from the curation doc):
    # category=exchange flow REVERSES the intuitive sign. A positive
    # net_flow_eth on the exchange category means users sent ETH TO
    # exchanges (sell pressure), NOT that the exchange itself bought ETH.
    # Read each category's sign per its own semantics.
    op.execute(
        """
        CREATE VIEW onchain.eth_whale_flows_aggregate AS
        SELECT
            category,
            (ts AT TIME ZONE 'UTC')::date AS day,
            COUNT(DISTINCT address)        AS address_count,
            SUM(balance_eth)               AS total_balance_eth,
            SUM(balance_usd_at_snapshot)   AS total_balance_usd,
            SUM(net_flow_eth_24h)          AS net_flow_eth,
            SUM(net_flow_usd_24h)          AS net_flow_usd,
            SUM(tx_count_24h)              AS tx_count
        FROM onchain.eth_whale_flows
        GROUP BY category, (ts AT TIME ZONE 'UTC')::date
        """
    )


def downgrade() -> None:
    """Drop the view first (depends on the table), then the table."""
    op.execute("DROP VIEW IF EXISTS onchain.eth_whale_flows_aggregate")
    op.execute("DROP TABLE IF EXISTS onchain.eth_whale_flows")
