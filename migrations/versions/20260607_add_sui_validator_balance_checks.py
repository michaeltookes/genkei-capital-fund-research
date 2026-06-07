"""Add missing non-negative checks for Sui validator balance columns.

Revision ID: b8c9d0e1f2a3
Revises: a6e7d8f9c012
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a6e7d8f9c012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add checks for nullable MIST balance columns on already-created tables."""
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'sui_validators_next_epoch_stake_mist_nonnegative'
            ) THEN
                ALTER TABLE onchain.sui_validators
                    ADD CONSTRAINT sui_validators_next_epoch_stake_mist_nonnegative
                    CHECK (
                        next_epoch_stake_mist IS NULL
                        OR next_epoch_stake_mist >= 0
                    );
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'sui_validators_rewards_pool_mist_nonnegative'
            ) THEN
                ALTER TABLE onchain.sui_validators
                    ADD CONSTRAINT sui_validators_rewards_pool_mist_nonnegative
                    CHECK (rewards_pool_mist IS NULL OR rewards_pool_mist >= 0);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Leave constraints in place because the base Sui table revision owns them."""
    # This migration backfills constraints for databases that already applied the
    # base table migration before the checks were added there. Fresh upgrades get
    # the same named constraints from a6e7d8f9c012, so downgrading only this
    # revision must preserve that revision's declared schema contract.
    pass
