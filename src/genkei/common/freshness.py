"""Shared data-freshness helpers (B-023).

Single source of truth for "how old is this data, and is it stale?" so a
second, divergent definition of "stale" can't creep in. Two freshness
signals live here:

* **snapshot freshness** — age of the freshest *data row* a read query
  returned (``prices``, ``tvl``). The daily candle/TVL row should be ~a
  day old; older means the ingest likely stalled.
* **ingest-run freshness** — age of the last successful *ingest run* for a
  source (``macro``). FRED series have mixed cadence (DGS10 daily,
  CPIAUCSL monthly, GDPC1 quarterly), so the freshest *observation* is
  legitimately weeks old for a monthly series — judging staleness by
  observation ts would false-positive. The right signal there is "when did
  we last pull FRED", which is daily regardless of series cadence and lives
  in ``meta.ingest_runs``.

``age_hours`` is the one place the ``(now - ts)`` arithmetic lives;
``genkei watchlist health`` / ``gaps`` call it too (B-044), so the read
path and the ops path agree on what an hour of staleness means.

The default threshold matches ``watchlist health``'s 36h — a daily cron
that ran 25h ago is healthy, so a 24h cutoff would false-positive every
time a job slips past midnight. Callers that read a slower series pass a
wider ``--max-snapshot-age-hours``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from genkei.common import db

# Matches the `watchlist health` / `gaps` stale cutoff. See module docstring
# for why 36h rather than 24h. These functions are plain helpers (not Typer
# commands), so their annotations stay lazy strings under
# `from __future__ import annotations` — PEP-604 `X | None` is safe on the
# 3.9 venv because it is never evaluated at runtime.
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 36.0


def _coerce_ts(last_ts: datetime | str | None) -> datetime | None:
    if last_ts is None:
        return None
    if isinstance(last_ts, str):
        last_ts = datetime.fromisoformat(last_ts)
    # Lake timestamps are UTC; treat a naive value as UTC so the
    # tz-aware ``now`` subtraction never raises.
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    return last_ts


def age_hours(last_ts: datetime | str | None, *, now: datetime | None = None) -> float | None:
    """Hours between ``last_ts`` and ``now`` (UTC), rounded to 1dp.

    Accepts a ``datetime`` or an ISO-8601 string (what the CLI readers carry
    in their row dicts). Returns ``None`` when ``last_ts`` is ``None`` so a
    no-data result is distinguishable from a fresh one.
    """
    ts = _coerce_ts(last_ts)
    if ts is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    return round((now - ts).total_seconds() / 3600, 1)


def _freshness(
    *,
    source: str,
    last_ts: datetime | str | None,
    max_age_hours: float,
    now: datetime | None,
) -> dict[str, Any]:
    age = age_hours(last_ts, now=now)
    ts = _coerce_ts(last_ts)
    return {
        "source": source,
        "last_ts": ts.isoformat() if ts is not None else None,
        "age_hours": age,
        "max_age_hours": max_age_hours,
        "stale": age is not None and age > max_age_hours,
    }


def snapshot_freshness(
    last_ts: datetime | str | None,
    *,
    source: str,
    max_age_hours: float = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Freshness of a freshest-row timestamp (``prices``/``tvl`` read path).

    ``source`` is a human label for the warning (e.g. ``"coingecko.market_data"``).
    Returns ``stale: False`` when ``last_ts`` is ``None`` — an empty result
    is a no-data condition the caller already messages, not a staleness one.
    """
    return _freshness(source=source, last_ts=last_ts, max_age_hours=max_age_hours, now=now)


def ingest_run_freshness(
    source: str,
    endpoint: str,
    *,
    max_age_hours: float = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Freshness of the last *successful* ``meta.ingest_runs`` row for a source.

    Used by the ``macro`` read path, where observation ts is the wrong
    staleness signal (mixed series cadence). The query lives here so no call
    site hand-writes ``meta.ingest_runs`` SQL.

    The DB probe is wrapped defensively: a freshness *warning* must never
    break the primary query it annotates. If the ``meta.ingest_runs`` lookup
    fails for any reason, we return a non-stale result (no warning) rather
    than propagate. In practice the main query has already succeeded by the
    time this runs, so a live connection is expected; the guard covers
    offline/edge cases (and keeps unit tests of the host command from needing
    to mock this secondary query).
    """
    try:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT max(started_at) FROM meta.ingest_runs "
                "WHERE source = %s AND endpoint = %s AND status = 'success'",
                [source, endpoint],
            )
            row = cur.fetchone()
        last_ts = row[0] if row else None
    except Exception:  # noqa: BLE001 — freshness must not break the query
        last_ts = None
    out = _freshness(
        source=f"{source}/{endpoint}",
        last_ts=last_ts,
        max_age_hours=max_age_hours,
        now=now,
    )
    out["kind"] = "ingest_run"
    return out


def stale_banner(freshness: dict[str, Any]) -> str:
    """One-line human warning for a stale-freshness dict."""
    return (
        f"⚠ STALE DATA: freshest {freshness['source']} is "
        f"{freshness['age_hours']}h old (threshold {freshness['max_age_hours']}h). "
        "The daily ingest may have stalled — check `genkei watchlist health`."
    )
