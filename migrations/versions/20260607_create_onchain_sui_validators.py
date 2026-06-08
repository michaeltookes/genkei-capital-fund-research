"""Create onchain.sui_validators per-epoch snapshot table (B-088).

Sui's consensus stake state is published as a *per-epoch snapshot* via
`suix_getLatestSuiSystemState` — one big system-state object containing
129+ active validators, each with current stake, pending flow, voting
power, commission, gas price, and lifecycle epochs. This table lands one
row per ``(epoch, validator_address)``.

Schema choice: NEW table, NOT extending ``onchain.staking_events`` with a
chain discriminator. The existing ``onchain.staking_events`` table is
event-shaped (B-082 LINK staking: per-event Staked / Unstaked / Unbonding
rows with ``event_type``, ``amount``, ``tx_hash``). Sui's data is
snapshot-shaped (per-validator-per-epoch state), structurally distinct.
The B-088 backlog spec offered "or extend onchain.staking_events with a
chain discriminator" — that path was rejected: jamming snapshot rows into
an event schema would force NULL columns half the time and corrupt the
event-stream semantics. Two tables in one ``onchain`` schema is the
cleaner fit.

**Backfill is NOT supported** for v1. The public Sui RPC only exposes the
current epoch's system state — no historical-epoch query method exists
(``suix_getEpochs`` returns ``Method not found`` on the public fullnode).
Forward-only ingest from the day of first run. Historical reconstruction
would require an indexer-side data path; deferred as a v2 follow-up.

Volume estimate: 129 validators × 365 epochs/year ≈ 47k rows/year. Tiny.
No partitioning, no hypertable conversion needed; plain table is fine.

Stake amounts are stored as MIST (Sui's atomic unit, 1 SUI = 10^9 MIST).
The column type is NUMERIC(40, 0) instead of BIGINT because Sui's max
supply (10B SUI = 10^19 MIST) exceeds BIGINT's max (~9.2×10^18). Today's
totalStake is ~7.25×10^18 MIST so BIGINT would survive — but a future
inflation event or a single validator capturing >85% of stake could
push past the boundary, and NUMERIC's ~16-byte-per-row cost is cheap
insurance against a corruption-causing overflow.

Revision ID: a6e7d8f9c012
Revises: f5d9c0e1a407
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a6e7d8f9c012"
down_revision: str | Sequence[str] | None = "f5d9c0e1a407"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the onchain.sui_validators snapshot table + lookup indexes."""
    # onchain schema already exists from B-082's migration (5d3e8b9c1a02).
    # CREATE IF NOT EXISTS defends against running this against a fresh
    # branch where the B-082 migration hasn't been applied yet.
    op.execute("CREATE SCHEMA IF NOT EXISTS onchain")

    op.execute(
        """
        CREATE TABLE onchain.sui_validators (
            epoch                          BIGINT         NOT NULL,
            epoch_start_ts                 TIMESTAMPTZ    NOT NULL,
            validator_address              TEXT           NOT NULL,
            name                           TEXT,
            voting_power                   INTEGER,
            stake_amount_mist              NUMERIC(40, 0) NOT NULL,
            next_epoch_stake_mist          NUMERIC(40, 0),
            pending_stake_mist             NUMERIC(40, 0) NOT NULL DEFAULT 0,
            pending_withdraw_mist          NUMERIC(40, 0) NOT NULL DEFAULT 0,
            commission_rate_bps            INTEGER,
            gas_price                      BIGINT,
            apy                            NUMERIC(10, 6),
            staking_pool_activation_epoch  BIGINT,
            staking_pool_deactivation_epoch BIGINT,
            rewards_pool_mist              NUMERIC(40, 0),
            source_endpoint                TEXT           NOT NULL,
            fetched_at                     TIMESTAMPTZ    NOT NULL DEFAULT now(),
            ingest_run_id                  BIGINT         NOT NULL REFERENCES meta.ingest_runs(id),
            PRIMARY KEY (epoch, validator_address),
            CHECK (stake_amount_mist >= 0),
            CONSTRAINT sui_validators_next_epoch_stake_mist_nonnegative
                CHECK (next_epoch_stake_mist IS NULL OR next_epoch_stake_mist >= 0),
            CHECK (pending_stake_mist >= 0),
            CHECK (pending_withdraw_mist >= 0),
            CONSTRAINT sui_validators_rewards_pool_mist_nonnegative
                CHECK (rewards_pool_mist IS NULL OR rewards_pool_mist >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX sui_validators_validator_epoch_idx "
        "ON onchain.sui_validators (validator_address, epoch DESC)"
    )
    op.execute(
        "CREATE INDEX sui_validators_epoch_idx "
        "ON onchain.sui_validators (epoch DESC)"
    )


def downgrade() -> None:
    """Drop the sui_validators table. Leave the onchain schema in place
    because B-082's staking_events table also lives there."""
    op.execute("DROP TABLE IF EXISTS onchain.sui_validators")
