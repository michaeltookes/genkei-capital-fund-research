"""Create onchain schema + onchain.staking_events (B-082).

Generic-by-design — Chainlink v0.2 is the first protocol we ingest
staking events for, but Lido / RocketPool / EigenLayer / etc. all
have the same shape (per-event row keyed on tx_hash + log_index,
with a protocol_slug column for filtering). One schema, many
protocols filtered by column rather than a `chainlink_*` schema that
locks us into a per-asset shape.

Schema design:
  - PK (tx_hash, log_index) — log_index uniquely identifies an event
    within a transaction; (tx_hash, log_index) globally identifies
    one log entry on Ethereum forever.
  - protocol_slug + chain together identify which contract emitted
    the event; the collector populates them from configuration so
    queries can scope to "all Chainlink staking activity" without
    joining on contract_address.
  - amount_token is the raw token amount (decimal-corrected from the
    contract's wei units — 1e18 for LINK).
  - amount_usd is optional; left NULL when no price snapshot is
    available at ingest time. A downstream join against
    coingecko.market_data can backfill USD values later.
  - Indexes: (protocol_slug, block_timestamp DESC) for "recent
    Chainlink activity"; (staker_address, block_timestamp DESC) for
    "this insider across protocols" — same query shapes as the SEC
    Form 4 indexes.
  - Hypertable on block_timestamp with 90-day chunks for the same
    reason sec.facts is a hypertable: long-horizon time-series with
    growth over time.

Revision ID: 5d3e8b9c1a02
Revises: 2c9f5e1d3a47
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "5d3e8b9c1a02"
down_revision: str | Sequence[str] | None = "2c9f5e1d3a47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS onchain")

    op.execute(
        """
        CREATE TABLE onchain.staking_events (
            tx_hash          TEXT        NOT NULL,
            log_index        INTEGER     NOT NULL,
            chain            TEXT        NOT NULL,
            protocol_slug    TEXT        NOT NULL,
            contract_address TEXT        NOT NULL,
            block_number     BIGINT      NOT NULL,
            block_timestamp  TIMESTAMPTZ NOT NULL,
            event_type       TEXT        NOT NULL,
            staker_address   TEXT        NOT NULL,
            amount_token     NUMERIC     NOT NULL,
            amount_usd       NUMERIC,
            source_endpoint  TEXT        NOT NULL,
            fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (tx_hash, log_index, block_timestamp)
        )
        """
    )
    op.execute(
        "CREATE INDEX staking_events_protocol_ts_idx "
        "ON onchain.staking_events (protocol_slug, block_timestamp DESC)"
    )
    op.execute(
        "CREATE INDEX staking_events_staker_idx "
        "ON onchain.staking_events (staker_address, block_timestamp DESC)"
    )

    # Hypertable + compression — same shape as sec.facts (R-019) so
    # long-horizon queries stay fast and old chunks compress.
    op.execute(
        """
        SELECT create_hypertable(
            'onchain.staking_events',
            'block_timestamp',
            chunk_time_interval => INTERVAL '90 days',
            if_not_exists => TRUE
        )
        """
    )
    op.execute(
        """
        ALTER TABLE onchain.staking_events SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'protocol_slug, staker_address',
            timescaledb.compress_orderby = 'block_timestamp DESC, log_index'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('onchain.staking_events', "
        "INTERVAL '90 days', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.execute(
        "SELECT remove_compression_policy('onchain.staking_events', if_exists => TRUE)"
    )
    op.execute("DROP TABLE IF EXISTS onchain.staking_events")
    op.execute("DROP SCHEMA IF EXISTS onchain")
