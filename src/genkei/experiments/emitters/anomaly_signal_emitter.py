"""Return-anomaly → signal_events emitter (B-069 follow-up).

Projects the flags in ``meta.anomalies`` (landed by the B-069 anomaly detector)
into ``meta.signal_events`` as a first-class **corroborating** signal source, so
a sharp one-day price move can *stack* with an insider cluster or a TVL-drawdown
in the B-064 correlator rather than living in its own silo. This is the piece
that lets "the price cracked AND insiders are selling" fire as one multi-source
stack.

Purely a transformer — the anomaly math already ran in the detector; this reads
the flags and re-emits them in the signal-event shape:

* ``source``      = ``"return_anomaly"`` (what the correlator rules match on).
* ``signal_kind`` = ``"return_spike"``.
* ``direction``   = ``"bearish"`` for a ``spike_down`` flag, ``"bullish"`` for
                    ``spike_up`` — so a downside spike stacks with the other
                    bearish sources (sell clusters, TVL stress).
* ``strength``    = saturating ramp on the anomaly's ``|modified z-score|``
                    (``|score| / SCORE_SATURATION`` clamped to 1.0): a
                    threshold-edge flag (|score|≈3.5) carries ~0.7, a violent
                    move (|score|≥5) carries 1.0.
* ``horizon``     = the asset's watchlist sleeve (``crypto:core`` /
                    ``crypto:tactical`` / ``equity:core``). Anomaly rows for
                    non-watchlist yahoo tickers (SPY, IBIT, …) resolve to no
                    sleeve and are skipped — signal events are for research
                    assets only.

Idempotent via the signal-event UNIQUE key
``(asset, ts, source, signal_kind, source_ref, horizon)`` — ``source_ref`` is
``"{asset}:{date}"``, so re-running over an overlapping ``--since`` window is a
no-op upsert.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, Watchlist, load_watchlist
from genkei.experiments.signal_store import emit_signals_bulk

EMITTER_SOURCE = "return_anomaly"
EMITTER_RUN_TAG = "signal_emitter"
EMITTER_ENDPOINT = "return_anomaly"
SIGNAL_KIND = "return_spike"
METRIC = "daily_return"

# |score| / SCORE_SATURATION clamped to [0, 1]. The detector's flag threshold is
# 3.5, so at 3.5 → 0.7 and at ≥5 → 1.0 — a threshold-edge flag still carries
# enough strength to combine with one other source over the rules' min_score.
SCORE_SATURATION = Decimal("5")

_DIRECTION = {"spike_up": "bullish", "spike_down": "bearish"}

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AnomalyFlag:
    asset: str
    asset_class: str
    ts: date
    value: Decimal
    score: Decimal
    method: str
    direction: str  # spike_up | spike_down
    window_days: int
    threshold: Decimal


@dataclass(frozen=True)
class EmitResult:
    """Return value of :func:`emit_return_anomaly_signals`."""

    ingest_run_id: int
    events_emitted: int
    flags_seen: int
    flags_skipped_no_horizon: int


def _load_flags(*, since: date | None, until: date | None) -> list[_AnomalyFlag]:
    sql = (
        "SELECT asset, asset_class, ts::date AS d, value, score, method, "
        "direction, window_days, threshold "
        "FROM meta.anomalies WHERE metric = %s"
    )
    params: list[Any] = [METRIC]
    if since is not None:
        sql += " AND ts::date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND ts::date <= %s"
        params.append(until)
    sql += " ORDER BY d ASC, asset"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        _AnomalyFlag(
            asset=asset,
            asset_class=cls,
            ts=d,
            value=value,
            score=score,
            method=method,
            direction=direction,
            window_days=int(window),
            threshold=threshold,
        )
        for (asset, cls, d, value, score, method, direction, window, threshold) in rows
    ]


def _strength_from_score(score: Decimal) -> Decimal:
    scaled = abs(score) / SCORE_SATURATION
    return scaled if scaled < Decimal("1") else Decimal("1")


def _crypto_by_coingecko_id(watchlist: Watchlist) -> dict[str, Any]:
    return {
        e.coingecko_id.strip().lower(): e
        for e in watchlist.crypto
        if e.coingecko_id and e.coingecko_id.strip()
    }


def _resolve_horizon(
    flag: _AnomalyFlag,
    *,
    crypto_by_cgid: dict[str, Any],
    watchlist: Watchlist,
) -> str | None:
    """Sleeve horizon for a flag's asset, or None if it's not a research asset.

    Crypto anomaly rows key on the coingecko_id (the detector's convention);
    equity rows key on the ticker. Non-watchlist yahoo tickers (benchmarks,
    ETF wrappers) resolve to None and are skipped.
    """
    if flag.asset_class == "crypto":
        entry = crypto_by_cgid.get(flag.asset.strip().lower())
        if entry is not None:
            return f"crypto:{entry.sleeve or 'core'}"
        return None
    if flag.asset_class == "equity":
        entry = watchlist.find_equity(flag.asset)
        if entry is not None:
            return f"equity:{entry.sleeve}"
        return None
    return None


def _build_event(flag: _AnomalyFlag, *, horizon: str) -> dict[str, Any]:
    ts = datetime.combine(flag.ts, time(0, 0, tzinfo=timezone.utc))
    return {
        "asset": flag.asset,
        "asset_class": flag.asset_class,
        "horizon": horizon,
        "ts": ts,
        "source": EMITTER_SOURCE,
        "signal_kind": SIGNAL_KIND,
        "direction": _DIRECTION[flag.direction],
        "strength": _strength_from_score(flag.score),
        "payload": {
            "metric": METRIC,
            "return_pct": str(flag.value * Decimal("100")),
            "score": str(flag.score),
            "method": flag.method,
            "anomaly_direction": flag.direction,
            "window_days": flag.window_days,
            "threshold": str(flag.threshold),
        },
        "source_ref": f"{flag.asset}:{flag.ts.isoformat()}",
    }


def emit_return_anomaly_signals(
    *,
    since: date | None = None,
    until: date | None = None,
    config: Path = DEFAULT_WATCHLIST_PATH,
) -> EmitResult:
    """Project ``meta.anomalies`` flags into ``meta.signal_events``."""
    watchlist = load_watchlist(config)
    crypto_by_cgid = _crypto_by_coingecko_id(watchlist)

    with db.ingest_run(
        EMITTER_RUN_TAG,
        endpoint=EMITTER_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
    ) as run:
        flags = _load_flags(since=since, until=until)
        events: list[dict[str, Any]] = []
        skipped_no_horizon = 0
        for flag in flags:
            horizon = _resolve_horizon(
                flag, crypto_by_cgid=crypto_by_cgid, watchlist=watchlist
            )
            if horizon is None:
                skipped_no_horizon += 1
                continue
            events.append(_build_event(flag, horizon=horizon))
        rows_written = emit_signals_bulk(events, ingest_run_id=run.id)
        run.add_rows(rows_written)
        LOGGER.info(
            "return_anomaly emitter wrote %s events (%s flags, %s skipped no-horizon)",
            rows_written,
            len(flags),
            skipped_no_horizon,
        )
        return EmitResult(
            ingest_run_id=run.id,
            events_emitted=rows_written,
            flags_seen=len(flags),
            flags_skipped_no_horizon=skipped_no_horizon,
        )


def parse_args(argv: list[str]) -> Any:
    import argparse

    def parse_date_arg(label: str) -> Any:
        def parse(raw: str) -> date | None:
            try:
                return _parse_date(raw, label=label)
            except Exception as exc:
                raise argparse.ArgumentTypeError(str(exc)) from exc

        return parse

    parser = argparse.ArgumentParser(
        description="Emit return-anomaly signal events into meta.signal_events."
    )
    parser.add_argument("--since", type=parse_date_arg("since"), default=None)
    parser.add_argument("--until", type=parse_date_arg("until"), default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    result = emit_return_anomaly_signals(since=args.since, until=args.until)
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "events_emitted": result.events_emitted,
                    "flags_seen": result.flags_seen,
                    "flags_skipped_no_horizon": result.flags_skipped_no_horizon,
                    "source": EMITTER_SOURCE,
                },
                default=_json_default,
            )
        )
    else:
        print(
            f"return_anomaly emitter wrote ingest_run_id={result.ingest_run_id} "
            f"events={result.events_emitted} flags={result.flags_seen} "
            f"skipped_no_horizon={result.flags_skipped_no_horizon}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
