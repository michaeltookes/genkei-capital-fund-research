"""Extend meta.signal_events.asset_class to allow 'macro' (B-096).

The macro-regime emitter (B-096) writes market-wide regime-transition
events under a sentinel asset ``MACRO``. A macro regime is genuinely not
an equity / crypto / protocol, so rather than mislabel it with one of the
existing classes we widen the CHECK to admit a fourth value. The original
constraint was created inline (unnamed) in the 20260528 table migration,
so Postgres auto-named it ``signal_events_asset_class_check``; we drop and
re-add it with the widened value set.

Blast radius is small: signal_benchmark.py already returns no benchmark
for any asset_class it doesn't recognize (macro has no price series), and
the digest groups by horizon, not asset_class.

Revision ID: c4d5e6f77889
Revises: a2b3c4d55667
Create Date: 2026-06-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c4d5e6f77889"
down_revision: str | Sequence[str] | None = "a2b3c4d55667"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE meta.signal_events
            DROP CONSTRAINT signal_events_asset_class_check,
            ADD CONSTRAINT signal_events_asset_class_check
                CHECK (asset_class IN ('equity', 'crypto', 'protocol', 'macro'))
        """
    )


def downgrade() -> None:
    # Reverting requires no 'macro' rows to remain, or the re-added
    # narrower CHECK will fail to validate. Emitted macro events must be
    # cleared before downgrading.
    op.execute(
        """
        ALTER TABLE meta.signal_events
            DROP CONSTRAINT signal_events_asset_class_check,
            ADD CONSTRAINT signal_events_asset_class_check
                CHECK (asset_class IN ('equity', 'crypto', 'protocol'))
        """
    )
