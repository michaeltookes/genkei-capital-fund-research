"""Per-series anomaly-detection runner (B-069).

Reads each watchlist asset's daily close series from the lake, converts it to
daily returns, runs the pure ``anomaly_detection`` detector over the whole
history, and reconciles the refreshed flag slice into ``meta.anomalies``.
``genkei anomalies`` reads them back.

Scope (v1): the metric is ``daily_return`` for every watchlist **crypto**
(via ``coinbase.candles``, keyed by coingecko_id) and **equity** (via
``yahoo.candles``, keyed by ticker) — the two densest, most-decision-relevant
price series. The detector itself is series-agnostic, so a TVL-level or
macro-level metric can be added later as another ``metric`` without touching
the table. JUP has no live Coinbase product and so has no close series to
scan; it (and any future product-less asset) is skipped with a warning.

The detector runs over the **full** loaded history so the rolling median/MAD
are well-formed, then ``--since`` / ``--until`` bound only which flagged dates
get persisted — the same "compute over everything, write the recent slice"
shape the TVL-drawdown emitter uses. Writes are idempotent on
``(asset, metric, ts, method)``, and stale rows in the refreshed
asset/metric/date slice are deleted before fresh flags are inserted, so the
daily cron can re-scan a trailing window harmlessly after source candles are
corrected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist
from genkei.experiments.anomaly_detection import (
    DEFAULT_MIN_WINDOW,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW,
    Anomaly,
    SeriesPoint,
    detect_anomalies,
    to_returns,
)
from genkei.experiments.signal_benchmark import load_close_series

SOURCE_NAME = "anomaly_detector"
ENDPOINT = "anomaly_detection"
METRIC_DAILY_RETURN = "daily_return"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanTarget:
    """One asset to scan: its lake identifier + class."""

    asset: str  # coingecko_id (crypto) or ticker (equity)
    asset_class: str  # 'crypto' | 'equity'


@dataclass(frozen=True)
class EmitResult:
    """Return value of ``run_anomaly_detection`` for CLI / test inspection."""

    ingest_run_id: int
    anomalies_written: int
    targets_scanned: int
    targets_skipped_no_data: int


@dataclass(frozen=True)
class TargetDetection:
    """Detection output plus the date slice refreshed for one target."""

    anomalies: list[Anomaly]
    refresh_since: date | None
    refresh_until: date | None


def _validate_window_config(*, window: int, min_window: int) -> None:
    if window <= 0:
        raise ValueError("window must be a positive integer")
    if min_window <= 0:
        raise ValueError("min_window must be a positive integer")
    if window < min_window:
        raise ValueError("window must be greater than or equal to min_window")


def _scan_targets() -> list[ScanTarget]:
    """Build the crypto + equity scan list from the watchlist."""
    watchlist = load_watchlist(DEFAULT_WATCHLIST_PATH)
    targets: list[ScanTarget] = []
    for entry in watchlist.crypto:
        # No Coinbase product → no close series in the lake to scan.
        if not entry.coinbase_product:
            LOGGER.warning(
                "anomaly_detector: crypto %s has no coinbase_product; skipping",
                entry.symbol,
            )
            continue
        targets.append(ScanTarget(asset=entry.coingecko_id.strip(), asset_class="crypto"))
    for equity in watchlist.equities:
        targets.append(ScanTarget(asset=equity.symbol.strip(), asset_class="equity"))
    return targets


def _ts(day: date) -> datetime:
    """A series date as a UTC-midnight timestamp for the TIMESTAMPTZ column."""
    return datetime.combine(day, time(0, 0, tzinfo=timezone.utc))


def _last_completed_utc_date() -> date:
    """Return the last fully completed UTC daily candle date."""
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def _anomaly_to_row(
    target: ScanTarget, anomaly: Anomaly, *, ingest_run_id: int
) -> dict[str, Any]:
    return {
        "asset": target.asset,
        "asset_class": target.asset_class,
        "metric": METRIC_DAILY_RETURN,
        "ts": _ts(anomaly.ts),
        "value": anomaly.value,
        "score": anomaly.score,
        "method": anomaly.method,
        "direction": anomaly.direction,
        "window_days": anomaly.window,
        "threshold": anomaly.threshold,
        "median": anomaly.median,
        "mad": anomaly.mad,
        "ingest_run_id": ingest_run_id,
    }


def _refresh_bounds(
    returns: list[SeriesPoint], *, since: date | None, until: date | None
) -> tuple[date | None, date | None]:
    """Return the inclusive date bounds that this run refreshed."""
    dates = [
        point.ts
        for point in returns
        if (since is None or point.ts >= since) and (until is None or point.ts <= until)
    ]
    if not dates:
        return None, None
    return dates[0], dates[-1]


def _detect_for_target(
    target: ScanTarget,
    *,
    since: date | None,
    until: date | None,
    window: int,
    threshold: Decimal,
    min_window: int,
) -> TargetDetection | None:
    """Load, transform, and detect for one target. ``None`` = no usable series."""
    closes = load_close_series(target.asset, target.asset_class, until=until)
    if len(closes) < min_window + 2:
        return None
    points = [SeriesPoint(ts=d, value=v) for d, v in closes]
    returns = to_returns(points)
    refresh_since, refresh_until = _refresh_bounds(returns, since=since, until=until)
    anomalies = detect_anomalies(
        returns, window=window, threshold=threshold, min_window=min_window
    )
    return TargetDetection(
        anomalies=[
            a
            for a in anomalies
            if (since is None or a.ts >= since) and (until is None or a.ts <= until)
        ],
        refresh_since=refresh_since,
        refresh_until=refresh_until,
    )


def _delete_refreshed_anomalies(
    conn: Any, detections: list[tuple[ScanTarget, TargetDetection]]
) -> int:
    """Delete stale anomaly flags in each target's refreshed date slice."""
    deleted = 0
    with conn.cursor() as cur:
        for target, detection in detections:
            if detection.refresh_since is None or detection.refresh_until is None:
                continue
            cur.execute(
                """
                DELETE FROM meta.anomalies
                WHERE asset = %s
                  AND asset_class = %s
                  AND metric = %s
                  AND ts >= %s
                  AND ts <= %s
                """,
                [
                    target.asset,
                    target.asset_class,
                    METRIC_DAILY_RETURN,
                    _ts(detection.refresh_since),
                    _ts(detection.refresh_until),
                ],
            )
            deleted += max(cur.rowcount or 0, 0)
    return deleted


def run_anomaly_detection(
    *,
    since: date | None = None,
    until: date | None = None,
    window: int = DEFAULT_WINDOW,
    threshold: Decimal = DEFAULT_THRESHOLD,
    min_window: int = DEFAULT_MIN_WINDOW,
) -> EmitResult:
    """Scan every watchlist price series and upsert flagged outliers."""
    _validate_window_config(window=window, min_window=min_window)
    effective_until = until or _last_completed_utc_date()
    targets = _scan_targets()
    with db.ingest_run(
        SOURCE_NAME,
        endpoint=ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": effective_until.isoformat(),
            "window": window,
            "threshold": str(threshold),
            "min_window": min_window,
            "metric": METRIC_DAILY_RETURN,
        },
    ) as run:
        rows: list[dict[str, Any]] = []
        detections: list[tuple[ScanTarget, TargetDetection]] = []
        skipped_no_data = 0
        for target in targets:
            detection = _detect_for_target(
                target,
                since=since,
                until=effective_until,
                window=window,
                threshold=threshold,
                min_window=min_window,
            )
            if detection is None:
                LOGGER.warning(
                    "anomaly_detector: %s (%s) has too little close history; skipping",
                    target.asset,
                    target.asset_class,
                )
                skipped_no_data += 1
                continue
            detections.append((target, detection))
            rows.extend(
                _anomaly_to_row(target, a, ingest_run_id=run.id)
                for a in detection.anomalies
            )
        written = 0
        if detections:
            with db.connection() as conn:
                deleted = _delete_refreshed_anomalies(conn, detections)
                if deleted:
                    LOGGER.info("anomaly_detector: deleted %s stale flags", deleted)
                if rows:
                    written = db.bulk_upsert(
                        conn,
                        "meta.anomalies",
                        rows,
                        conflict_keys=("asset", "metric", "ts", "method"),
                    )
        run.add_rows(written)
        LOGGER.info(
            "anomaly_detector: +%s flags across %s targets (%s skipped no-data)",
            written,
            len(targets) - skipped_no_data,
            skipped_no_data,
        )
        return EmitResult(
            ingest_run_id=run.id,
            anomalies_written=written,
            targets_scanned=len(targets) - skipped_no_data,
            targets_skipped_no_data=skipped_no_data,
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
        description="Detect per-series return anomalies into meta.anomalies."
    )
    parser.add_argument("--since", type=parse_date_arg("since"), default=None)
    parser.add_argument(
        "--until",
        type=parse_date_arg("until"),
        default=_last_completed_utc_date(),
    )
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--threshold", type=Decimal, default=DEFAULT_THRESHOLD)
    parser.add_argument("--min-window", type=int, default=DEFAULT_MIN_WINDOW)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        _validate_window_config(window=args.window, min_window=args.min_window)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    result = run_anomaly_detection(
        since=args.since,
        until=args.until,
        window=args.window,
        threshold=args.threshold,
        min_window=args.min_window,
    )
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "anomalies_written": result.anomalies_written,
                    "targets_scanned": result.targets_scanned,
                    "targets_skipped_no_data": result.targets_skipped_no_data,
                    "source": SOURCE_NAME,
                },
                default=_json_default,
            )
        )
    else:
        print(
            f"anomaly_detector wrote ingest_run_id={result.ingest_run_id} "
            f"anomalies={result.anomalies_written} "
            f"targets_scanned={result.targets_scanned} "
            f"targets_skipped_no_data={result.targets_skipped_no_data}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
