"""Create meta.alerts — threshold-based alert store (B-068).

The alert engine (``genkei.experiments.alert_engine``) sits one layer above
the cross-source correlator (B-064). The correlator's rules decide what counts
as a *stack* (multi-source agreement on an asset); the alert engine's
thresholds decide which of those stacks are worth *paging about*, and lands one
row here per (alert-rule, stack) that clears the bar. Surfaced read-only via
``genkei alerts``; a daily workflow runs the engine and relays new rows to the
Discord/GitHub-issue notification path (B-119).

Schema design choices:

  * **``alert_id BIGSERIAL`` surrogate PK** — synthetic, same as
    ``meta.signal_events``. The natural dedup key is ``fingerprint`` (below),
    kept as a separate UNIQUE so re-running the engine over an overlapping
    date window is an idempotent no-op rather than a duplicate page.
  * **``fingerprint`` UNIQUE** —
    ``{alert_rule}:{correlation_rule}:{asset}:{horizon}:{triggered_at date}``.
    A stack's ``window_end`` (→ ``triggered_at``) is stable, so the same stack
    always hashes to the same fingerprint and ``ON CONFLICT DO NOTHING`` makes
    the daily run replay-safe. A genuinely new stack on a later date gets a new
    fingerprint → a new alert.
  * **``alert_rule`` vs ``correlation_rule``** — two names on purpose:
    ``correlation_rule`` is the B-064 signal rule whose stack fired (e.g.
    ``broad_exit``); ``alert_rule`` is the B-068 threshold that escalated it
    (e.g. ``critical_equity_exit``). One stack can trip several alert rules.
  * **``severity`` CHECK** — ``info`` / ``warning`` / ``critical``. Drives how
    loudly the notification hook pages (Discord embed color, issue-or-not).
  * **``asset_class`` CHECK** mirrors the (post-B-066) signal-events set so a
    macro- or protocol-scoped stack can raise an alert too.
  * **``status``** — ``open`` / ``acknowledged`` / ``resolved``. Defaults to
    ``open``; the ack/resolve workflow is a follow-up, but the column exists so
    the lister can filter from day one without a later migration.
  * **``notified_at`` nullable** — stamped when the notification hook actually
    delivered the row, so a re-run doesn't double-notify and an operator can
    see which alerts were paged vs only persisted.
  * **``payload`` JSONB** — the stack summary (component events, window span,
    benchmark if computed). The engine writes it; the CLI / agent read it.
  * **Plain table, not a hypertable.** Only *pageable* stacks land — a handful
    per week by construction — so chunking never pays. Same call
    ``meta.signal_events`` / ``meta.anomalies`` made.

Indexes:
  * ``(triggered_at DESC)``            — "what paged most recently"
  * ``(asset, triggered_at DESC)``     — per-asset alert history
  * ``(status, severity)``             — "open criticals" operator view

Revision ID: a2b7c8d09e13
Revises: f9b2c3d45e6a
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a2b7c8d09e13"
down_revision: str | Sequence[str] | None = "f9b2c3d45e6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meta.alerts (
            alert_id         BIGSERIAL    PRIMARY KEY,
            alert_rule       TEXT         NOT NULL,
            correlation_rule TEXT         NOT NULL,
            asset            TEXT         NOT NULL,
            asset_class      TEXT         NOT NULL CHECK (
                                 asset_class IN ('equity', 'crypto', 'protocol', 'macro')
                             ),
            horizon          TEXT         NOT NULL,
            direction        TEXT         NOT NULL CHECK (
                                 direction IN ('bullish', 'bearish', 'neutral')
                             ),
            severity         TEXT         NOT NULL CHECK (
                                 severity IN ('info', 'warning', 'critical')
                             ),
            score            NUMERIC      NOT NULL,
            distinct_sources INTEGER      NOT NULL CHECK (distinct_sources >= 0),
            triggered_at     TIMESTAMPTZ  NOT NULL,
            fingerprint      TEXT         NOT NULL UNIQUE,
            status           TEXT         NOT NULL DEFAULT 'open' CHECK (
                                 status IN ('open', 'acknowledged', 'resolved')
                             ),
            payload          JSONB        NOT NULL DEFAULT '{}'::jsonb,
            notified_at      TIMESTAMPTZ,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
            ingest_run_id    BIGINT       NOT NULL REFERENCES meta.ingest_runs(id)
        )
        """
    )
    op.execute("CREATE INDEX alerts_triggered_at_idx ON meta.alerts (triggered_at DESC)")
    op.execute(
        "CREATE INDEX alerts_asset_triggered_at_idx "
        "ON meta.alerts (asset, triggered_at DESC)"
    )
    op.execute(
        "CREATE INDEX alerts_status_severity_idx ON meta.alerts (status, severity)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meta.alerts")
