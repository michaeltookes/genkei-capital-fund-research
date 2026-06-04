"""Dedupe defillama.* + day-align ts (B-109).

Cleanup migration for the double-ingest bug found 2026-06-04. Root
cause: ``normalize_stablecoins`` / ``normalize_protocol_history`` /
``normalize_protocol_fee_series`` / ``normalize_stablecoin_history``
wrote ``ts`` with intra-day precision. The natural-key PKs on
``(asset_id, chain, ts)`` / ``(slug, chain, ts)`` / ``(slug, ts)``
couldn't dedupe because the same logical day arrived under different
``ts`` values across runs (e.g. the daily collector landing
``05:51:47 UTC`` and the B-085 stablecoin backfill landing
``00:00 UTC`` for the same logical day → both rows persist).

The normalize-layer fix lands in the same commit series as this
migration; this migration cleans up the historical data accumulated
between 2026-05-10 (when the duplication started) and the rollout.

Strategy per table (run in this order to avoid PK violations):

  1. **Dedupe** — for every group of rows sharing the same logical day
     under the natural key, keep the row with the highest
     ``ingest_run_id`` (the most recent normalize run's value wins) and
     ``DELETE`` the rest. Same as the ``DISTINCT ON (... ORDER BY
     ingest_run_id DESC)`` workaround the ``genkei stablecoin-flow``
     CLI was applying at query time.
  2. **Day-align** — UPDATE the remaining rows' ``ts`` to UTC midnight
     of the day each value represents. Steps 1 and 2 cannot be
     combined into a single statement because the UPDATE would PK-collide
     with the (still-undeduped) duplicates.

After this migration, the existing PKs + the normalize-layer day-align
fix together guarantee per-day uniqueness going forward.

``defillama.chain_tvl`` and ``defillama.prices`` are NOT affected by
this bug (their normalizers already write day-aligned ts), so this
migration leaves them untouched.

Revision ID: b0d2e3f4a5c7
Revises: c9a4e7b21f08
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b0d2e3f4a5c7"
down_revision: str | Sequence[str] | None = "c9a4e7b21f08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Per-table dedupe + day-align SQL. Each table follows the same shape:
#   1. CTE ranks rows within the natural-key + ts::date group by
#      ingest_run_id DESC; we identify rows by the natural-key tuple
#      itself (PK on the table) rather than ``ctid``. TimescaleDB
#      compressed chunks don't expose ``ctid`` (only ``tableoid`` is
#      supported as a system column on decompressed scans), so using
#      the natural-key tuple is the portable identifier.
#   2. DELETE rows with rank > 1 (keep the latest run per logical day).
#   3. UPDATE remaining rows so ts = date_trunc('day', ts AT TIME ZONE
#      'UTC') AT TIME ZONE 'UTC' (canonical UTC midnight).
#
# Step 3 may still PK-violate if the original PK row at canonical-midnight
# already exists — fall back: skip those updates and DELETE the
# now-redundant non-canonical row. The deletion-on-conflict pattern is
# expressed as a NOT EXISTS guard in the UPDATE plus a follow-up DELETE
# of rows whose ts is not yet midnight after the UPDATE.

_DEDUPE_STABLECOINS = """
    DELETE FROM defillama.stablecoins t
    WHERE (t.asset_id, t.chain, t.ts) IN (
        SELECT asset_id, chain, ts FROM (
            SELECT asset_id, chain, ts,
                   ROW_NUMBER() OVER (
                       PARTITION BY asset_id, chain,
                                    date_trunc('day', ts AT TIME ZONE 'UTC')
                       ORDER BY ingest_run_id DESC
                   ) AS rn
            FROM defillama.stablecoins
        ) ranked
        WHERE rn > 1
    )
"""

_ALIGN_STABLECOINS = """
    UPDATE defillama.stablecoins AS t
    SET ts = date_trunc('day', t.ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    WHERE t.ts <> date_trunc('day', t.ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
      AND NOT EXISTS (
          SELECT 1 FROM defillama.stablecoins u
          WHERE u.asset_id = t.asset_id
            AND u.chain = t.chain
            AND u.ts = date_trunc('day', t.ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
      )
"""

# Sweep up any non-canonical rows that the UPDATE's NOT EXISTS guard
# skipped (canonical row already there) — they're redundant duplicates.
_SWEEP_STABLECOINS = """
    DELETE FROM defillama.stablecoins
    WHERE ts <> date_trunc('day', ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
"""

_DEDUPE_PROTOCOL_TVL = """
    DELETE FROM defillama.protocol_tvl t
    WHERE (t.slug, t.chain, t.ts) IN (
        SELECT slug, chain, ts FROM (
            SELECT slug, chain, ts,
                   ROW_NUMBER() OVER (
                       PARTITION BY slug, chain,
                                    date_trunc('day', ts AT TIME ZONE 'UTC')
                       ORDER BY ingest_run_id DESC
                   ) AS rn
            FROM defillama.protocol_tvl
        ) ranked
        WHERE rn > 1
    )
"""

_ALIGN_PROTOCOL_TVL = """
    UPDATE defillama.protocol_tvl AS t
    SET ts = date_trunc('day', t.ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    WHERE t.ts <> date_trunc('day', t.ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
      AND NOT EXISTS (
          SELECT 1 FROM defillama.protocol_tvl u
          WHERE u.slug = t.slug
            AND u.chain = t.chain
            AND u.ts = date_trunc('day', t.ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
      )
"""

_SWEEP_PROTOCOL_TVL = """
    DELETE FROM defillama.protocol_tvl
    WHERE ts <> date_trunc('day', ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
"""

_DEDUPE_PROTOCOL_FEES = """
    DELETE FROM defillama.protocol_fees t
    WHERE (t.slug, t.ts) IN (
        SELECT slug, ts FROM (
            SELECT slug, ts,
                   ROW_NUMBER() OVER (
                       PARTITION BY slug,
                                    date_trunc('day', ts AT TIME ZONE 'UTC')
                       ORDER BY ingest_run_id DESC
                   ) AS rn
            FROM defillama.protocol_fees
        ) ranked
        WHERE rn > 1
    )
"""

_ALIGN_PROTOCOL_FEES = """
    UPDATE defillama.protocol_fees AS t
    SET ts = date_trunc('day', t.ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    WHERE t.ts <> date_trunc('day', t.ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
      AND NOT EXISTS (
          SELECT 1 FROM defillama.protocol_fees u
          WHERE u.slug = t.slug
            AND u.ts = date_trunc('day', t.ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
      )
"""

_SWEEP_PROTOCOL_FEES = """
    DELETE FROM defillama.protocol_fees
    WHERE ts <> date_trunc('day', ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
"""


def upgrade() -> None:
    # Disable TimescaleDB's per-transaction tuple-decompression limit for
    # this one-shot data migration. The defillama hypertables have
    # compressed chunks older than 30 days; the dedupe + day-align
    # touches every row (945k stablecoins / 116k protocol_tvl / 16k
    # protocol_fees), which exceeds the default safety limit (~100k).
    # Setting to 0 = unlimited for the duration of this session only.
    op.execute(
        "SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0"
    )
    # defillama.stablecoins
    op.execute(_DEDUPE_STABLECOINS)
    op.execute(_ALIGN_STABLECOINS)
    op.execute(_SWEEP_STABLECOINS)
    # defillama.protocol_tvl
    op.execute(_DEDUPE_PROTOCOL_TVL)
    op.execute(_ALIGN_PROTOCOL_TVL)
    op.execute(_SWEEP_PROTOCOL_TVL)
    # defillama.protocol_fees
    op.execute(_DEDUPE_PROTOCOL_FEES)
    op.execute(_ALIGN_PROTOCOL_FEES)
    op.execute(_SWEEP_PROTOCOL_FEES)


def downgrade() -> None:
    # The dedupe + day-align is irreversible by design: we deleted
    # redundant rows and lossily collapsed intra-day ts values to
    # midnight. Downgrade is a no-op rather than re-introducing
    # known-bad data. Roll back the normalize-layer fix on the code
    # side if the schema needs to revert.
    pass
