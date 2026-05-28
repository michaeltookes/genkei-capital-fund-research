"""Create meta.signal_events — atomic per-event signal store (B-064).

This is the *event-level* signal store that the cross-source
correlation engine reads from. It's deliberately *separate* from the
pre-existing ``meta.signals`` table (B-065's watchlist scoring rubric
output) — those two artifacts answer different questions:

  * ``meta.signals``        — one row per (asset, day, rubric_version)
                              with a composite *score* and per-component
                              breakdown. Summarized state.
  * ``meta.signal_events``  — one row per *event* fired by any Phase 5
                              experiment. Atomic, time-stamped, and
                              source-tagged. The correlator scans
                              these for co-occurrence.

Naming reads cleanly side-by-side: ``signals`` is the score, ``signal_events``
is the underlying stream. Renaming the existing ``meta.signals`` to free
up the cleaner name would have been hostile to B-065's already-deployed
daily workflow.

Schema design choices:

  * **``event_id BIGSERIAL`` surrogate PK** — synthetic. Lets a single
    natural-key clash (e.g. an experiment re-emits the same event with
    a different payload during a backfill) resolve via the
    ``(asset, ts, source, signal_kind, source_ref)`` UNIQUE constraint
    and ``ON CONFLICT DO UPDATE`` rather than blowing up.
  * **``asset`` + ``asset_class``** mirror the watchlist-scoring shape
    (the ``meta.signals`` rows already use this convention). Crypto
    assets use the CoinGecko id; equities use the ticker; protocols
    use the DeFiLlama slug.
  * **``source`` + ``signal_kind`` discriminators** — two levels of
    discriminator so a single experiment can emit multiple kinds. For
    example ``source='insider_clusters'`` paired with either
    ``signal_kind='buy_cluster'`` or ``signal_kind='sell_cluster'``.
  * **``direction`` CHECK** — every event has a directional bias the
    correlator can stack. ``neutral`` exists for events that are
    informational but not directional (e.g. regime changes that affect
    sleeves differently — the correlator handles those via rules
    rather than baking direction in).
  * **``strength`` NUMERIC nullable** — 0-1 confidence/intensity for
    rule scoring. Nullable because some emitters (e.g. macro regime
    label) don't have a natural strength axis; the correlator
    defaults missing strength to 1.0 when scoring.
  * **``payload`` JSONB**, defaulted to ``'{}'``. Source-specific event
    details — cluster members, item code, $ value, drawdown threshold,
    etc. The correlator doesn't read it; it's there for the CLI / agent.
  * **``source_ref``** — natural identifier in the upstream source.
    For SEC events it's the ``accession_number``; for crowding events
    it's ``<filer_cik>:<cusip>:<period>``. Carries enough to dedupe
    across re-emissions.
  * **Plain table, not hypertable.** Volume estimate: 7 emitters × at
    most a few events per asset per quarter × 35 watchlist assets ×
    ~30 years ≈ 200k rows steady-state. Plain-PG range. Same call
    ``sec.filings`` made (see ``20260510_create_sec_schema.py``).

Indexes:
  * ``(asset, ts DESC)``       — "what fired on AAPL since X"
  * ``(ts DESC)``              — "what fired across the watchlist today"
  * ``(source, ts DESC)``      — per-source backfill / re-emission
  * ``(direction, ts DESC)``   — directional scans (bullish-only / bearish-only)

Revision ID: d8e1f2a3b405
Revises: a1f3e8d20571
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d8e1f2a3b405"
down_revision: str | Sequence[str] | None = "a1f3e8d20571"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meta.signal_events (
            event_id      BIGSERIAL    PRIMARY KEY,
            asset         TEXT         NOT NULL,
            asset_class   TEXT         NOT NULL CHECK (
                              asset_class IN ('equity', 'crypto', 'protocol')
                          ),
            ts            TIMESTAMPTZ  NOT NULL,
            source        TEXT         NOT NULL,
            signal_kind   TEXT         NOT NULL,
            direction     TEXT         NOT NULL CHECK (
                              direction IN ('bullish', 'bearish', 'neutral')
                          ),
            strength      NUMERIC,
            payload       JSONB        NOT NULL DEFAULT '{}'::jsonb,
            source_ref    TEXT,
            computed_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            ingest_run_id BIGINT       NOT NULL REFERENCES meta.ingest_runs(id),
            UNIQUE (asset, ts, source, signal_kind, source_ref)
        )
        """
    )
    op.execute(
        "CREATE INDEX signal_events_asset_ts_idx ON meta.signal_events (asset, ts DESC)"
    )
    op.execute(
        "CREATE INDEX signal_events_ts_idx ON meta.signal_events (ts DESC)"
    )
    op.execute(
        "CREATE INDEX signal_events_source_ts_idx ON meta.signal_events (source, ts DESC)"
    )
    op.execute(
        "CREATE INDEX signal_events_direction_ts_idx "
        "ON meta.signal_events (direction, ts DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meta.signal_events")
