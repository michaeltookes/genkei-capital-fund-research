"""Insider-clusters → signal_events emitter (B-064 reference emitter).

Adapts B-060's ``detect_clusters`` output into atomic signal events
suitable for the cross-source correlator. Each detected cluster
becomes one ``meta.signal_events`` row:

* ``asset``         = issuer ticker (one event per watchlist ticker
                      resolved via ``cik`` lookup; clusters on
                      non-watchlist issuers are logged + skipped because
                      the correlator's asset-grouping needs a stable
                      identifier and we don't want to leak raw CIKs into
                      the events table)
* ``ts``            = ``cluster.window_end`` (the cluster becomes
                      actionable when the *last* reporter has filed)
* ``source``        = ``"insider_clusters"``
* ``signal_kind``   = ``"buy_cluster"`` or ``"sell_cluster"``
* ``direction``     = ``"bullish"`` (buys) or ``"bearish"`` (sells)
* ``strength``      = ``min(reporter_count / 5, 1.0)`` — a 5-reporter
                      cluster is full conviction; anything above stays
                      saturated. Tunable but baked into a constant here
                      so changes are obvious in git history.
* ``horizon``       = watchlist-derived equity sleeve tag.
* ``source_ref``    = ``"<issuer_cik>:<window_end_iso>"``
                      Carries the natural identifier of the cluster.
                      The UNIQUE constraint on
                      ``(asset, ts, source, signal_kind, source_ref, horizon)``
                      makes re-emission idempotent — a re-run of the
                      detector against the same data lands the same
                      source_ref and updates the payload in place.
* ``payload``       = full cluster details (reporters, total value,
                      window span). The correlator doesn't read it;
                      the CLI / agent does.

Run as one ``meta.ingest_runs`` row tagged
``source='signal_emitter' endpoint='insider_clusters'`` so the
provenance trail is uniform with the rest of the lake.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from genkei.common import db
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EquityEntry,
    Watchlist,
    load_watchlist,
)
from genkei.experiments.insider_clusters import (
    Cluster,
    detect_clusters,
    query_buy_candidates,
    query_sell_candidates,
)
from genkei.experiments.signal_store import emit_signals_bulk

EMITTER_SOURCE = "insider_clusters"
EMITTER_RUN_TAG = "signal_emitter"
EMITTER_ENDPOINT = "insider_clusters"
STRENGTH_SATURATION_REPORTERS = Decimal("5")
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmitResult:
    """Return value of ``emit_recent_clusters`` for CLI / test inspection."""

    ingest_run_id: int
    clusters_emitted: int
    clusters_skipped_no_ticker: int


def _ticker_by_cik(watchlist: Watchlist) -> dict[str, list[EquityEntry]]:
    out: dict[str, list[EquityEntry]] = defaultdict(list)
    for entry in watchlist.equities:
        if entry.cik is not None:
            out[entry.cik].append(entry)
    return dict(out)


def _strength_from_reporter_count(reporter_count: int) -> Decimal:
    """Map reporter_count to a 0-1 strength via a saturating ramp.

    1 reporter is below detector threshold; 2 → 0.4; 5 → 1.0; any
    more saturates at 1.0. The constant lives in this module so
    bumping the saturation point shows up in git blame on this file
    rather than in a faraway YAML.
    """
    if reporter_count <= 0:
        return Decimal("0")
    value = Decimal(reporter_count) / STRENGTH_SATURATION_REPORTERS
    return value if value < Decimal("1") else Decimal("1")


def _window_end_to_ts(window_end: date) -> datetime:
    """Convert the cluster's end *date* to a UTC datetime for the events table."""
    return datetime.combine(window_end, time(0, 0, tzinfo=timezone.utc))


def _horizon_tag(equity: EquityEntry) -> str:
    return f"equity:{equity.sleeve}"


def _build_events(
    cluster: Cluster, ticker_by_cik: dict[str, list[EquityEntry]]
) -> list[dict[str, Any]]:
    """Map one Cluster to one signal event per watchlist ticker for its CIK."""
    equities = ticker_by_cik.get(cluster.issuer_cik)
    if not equities:
        LOGGER.warning(
            "insider-cluster on CIK %s not in equity watchlist; skipping signal emission",
            cluster.issuer_cik,
        )
        return []
    direction = "bullish" if cluster.direction == "buy" else "bearish"
    signal_kind = "buy_cluster" if cluster.direction == "buy" else "sell_cluster"
    ts = _window_end_to_ts(cluster.window_end)
    source_ref = f"{cluster.issuer_cik}:{cluster.window_end.isoformat()}"
    events: list[dict[str, Any]] = []
    for equity in equities:
        payload: dict[str, Any] = {
            "issuer_cik": cluster.issuer_cik,
            "ticker": equity.symbol,
            "sleeve": equity.sleeve,
            "tier": equity.tier,
            "window_start": cluster.window_start.isoformat(),
            "window_end": cluster.window_end.isoformat(),
            "span_days": (cluster.window_end - cluster.window_start).days,
            "reporter_count": cluster.reporter_count,
            "total_shares": str(cluster.total_shares),
            "total_value_usd": (
                str(cluster.total_value_usd)
                if cluster.total_value_usd is not None
                else None
            ),
            "reporters": [
                {
                    "reporter_cik": r.reporter_cik,
                    "reporter_name": r.reporter_name,
                    "shares": str(r.shares),
                    "value_usd": str(r.value_usd) if r.value_usd is not None else None,
                    "is_officer": r.is_officer,
                    "is_director": r.is_director,
                    "is_ten_percent_owner": r.is_ten_percent_owner,
                    "officer_title": r.officer_title,
                }
                for r in cluster.reporters
            ],
        }
        events.append(
            {
                "asset": equity.symbol,
                "asset_class": "equity",
                "horizon": _horizon_tag(equity),
                "ts": ts,
                "source": EMITTER_SOURCE,
                "signal_kind": signal_kind,
                "direction": direction,
                "strength": _strength_from_reporter_count(cluster.reporter_count),
                "payload": payload,
                "source_ref": source_ref,
            }
        )
    return events


def emit_recent_clusters(
    *,
    since: date | None = None,
    until: date | None = None,
    min_reporters: int = 2,
    window_days: int = 7,
    config: Path = DEFAULT_WATCHLIST_PATH,
) -> EmitResult:
    """Detect buy + sell clusters in the date range and emit signal events.

    Runs in a single ``meta.ingest_runs`` row so the emitter's progress
    is queryable via ``genkei watchlist health`` like any other source.
    """
    watchlist = load_watchlist(config)
    ticker_by_cik = _ticker_by_cik(watchlist)

    with db.ingest_run(
        EMITTER_RUN_TAG,
        endpoint=EMITTER_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "min_reporters": min_reporters,
            "window_days": window_days,
        },
    ) as run:
        buy_txns = query_buy_candidates(since=since, until=until)
        sell_txns = query_sell_candidates(since=since, until=until)
        buy_clusters = detect_clusters(
            buy_txns,
            direction="buy",
            min_reporters=min_reporters,
            window_days=window_days,
        )
        sell_clusters = detect_clusters(
            sell_txns,
            direction="sell",
            min_reporters=min_reporters,
            window_days=window_days,
        )
        events: list[dict[str, Any]] = []
        skipped = 0
        for cluster in (*buy_clusters, *sell_clusters):
            rows = _build_events(cluster, ticker_by_cik)
            if not rows:
                skipped += 1
                continue
            events.extend(rows)
        rows_written = emit_signals_bulk(events, ingest_run_id=run.id)
        run.add_rows(rows_written)
        return EmitResult(
            ingest_run_id=run.id,
            clusters_emitted=rows_written,
            clusters_skipped_no_ticker=skipped,
        )


def parse_args(argv: list[str]) -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        description="Emit insider-cluster signal events into meta.signal_events."
    )
    parser.add_argument("--since", type=date.fromisoformat, default=None)
    parser.add_argument("--until", type=date.fromisoformat, default=None)
    parser.add_argument("--min-reporters", type=int, default=2)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    result = emit_recent_clusters(
        since=args.since,
        until=args.until,
        min_reporters=args.min_reporters,
        window_days=args.window_days,
    )
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "clusters_emitted": result.clusters_emitted,
                    "clusters_skipped_no_ticker": result.clusters_skipped_no_ticker,
                    "source": EMITTER_SOURCE,
                }
            )
        )
    else:
        print(
            f"insider-cluster emitter wrote ingest_run_id={result.ingest_run_id} "
            f"clusters={result.clusters_emitted} skipped={result.clusters_skipped_no_ticker}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
