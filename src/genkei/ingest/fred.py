"""FRED macro-series collector (B-028).

Fetches series metadata + full-vintage observations for every series
configured under ``macro_series:`` in ``config/watchlists.yml``. Lands
two raw blobs per series (``series_<id>``, ``observations_<id>``) in
``meta.raw_blobs``. The downstream normalizer
(``genkei.normalize.fred``) reads from those blobs.

Incremental design: each daily run fetches only the vintages newer than
what's already stored — ``realtime_start = max(realtime_start) in
fred.observations`` for the series, ``realtime_end = 9999-12-31`` — so new
revisions land as new rows under D-013's ``(series_id, ts, realtime_start)``
schema rather than overwriting history. ``--backfill`` re-requests the full
history from ``EARLIEST_REALTIME``.

Vintage handling (the G-019/G-027/G-029 history): observations calls send a
*bounded* realtime window. Omitting the window defaults to today's vintage
and collapses the vintage-aware table into snapshot duplication; the
full-history window ``1776-07-04 → 9999-12-31`` returns HTTP 400 on long
daily series (FRED caps full-window JSON at 2000 vintages). The incremental
window sidesteps both — it spans only the handful of vintages since the last
collect, far under the cap, and ``realtime_end=9999`` keeps current values'
vintage windows un-clipped. A prior ``output_type=3`` vintage-chunk approach
was reverted: it re-fetched all ~5,000 vintages per daily series each run
(hung past the workflow timeout, 2026-05-16 → 2026-06-19) and emitted a wide
vintage-column shape the normalizer couldn't parse.

API key: the free FRED API key lives in the ``FRED_API_KEY`` env var.
Register at https://fredaccount.stlouisfed.org/apikeys.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist

SOURCE_NAME = "fred"
COLLECT_ENDPOINT_LABEL = "collect"
FRED_BASE_URL = "https://api.stlouisfed.org/fred"
SERIES_BLOB_PREFIX = "series_"
OBSERVATIONS_BLOB_PREFIX = "observations_"
# Free-tier rate limit is documented at 120 req/min. We stay well under
# (1 req / sec) since 20 series × 2 calls = 40 calls per run completes
# in under a minute either way.
DEFAULT_RATE_LIMIT = RateLimit.per_second(1)
# Earliest documented FRED realtime_start.
EARLIEST_REALTIME = "1776-07-04"
LATEST_REALTIME = "9999-12-31"
OBSERVATIONS_PAGE_LIMIT = 100_000
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeriesTarget:
    """A FRED series we want to fetch."""

    series_id: str
    name: str
    rationale: str | None = None


def load_series(path: Path) -> list[SeriesTarget]:
    """Read ``macro_series:`` from watchlists.yml as ``SeriesTarget``s.

    Rejects duplicate series IDs since the FRED collector keys on them and
    a duplicate would silently double-fetch.
    """
    try:
        watchlist = load_watchlist(path)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not watchlist.macro:
        raise SystemExit("watchlists.yml is missing macro_series or it is empty.")
    out: list[SeriesTarget] = []
    seen_ids: set[str] = set()
    for entry in watchlist.macro:
        if entry.series_id in seen_ids:
            raise SystemExit(f"Duplicate macro_series id: {entry.series_id}")
        seen_ids.add(entry.series_id)
        if not entry.name:
            raise SystemExit(
                f"macro_series entry {entry.series_id!r} is missing a string `name`."
            )
        out.append(
            SeriesTarget(
                series_id=entry.series_id,
                name=entry.name,
                rationale=entry.rationale,
            )
        )
    return out


def require_api_key() -> str:
    """Return ``FRED_API_KEY`` from the environment or raise."""
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise SystemExit(
            "FRED_API_KEY is not set. Register a free key at "
            "https://fredaccount.stlouisfed.org/apikeys and set it in your "
            "local .env or as a GitHub Actions secret."
        )
    return key


def build_series_url(api_key: str, series_id: str) -> str:
    """Build the URL for the series-metadata endpoint."""
    return f"{FRED_BASE_URL}/series?series_id={series_id}&api_key={api_key}&file_type=json"


def build_observations_url(
    api_key: str,
    series_id: str,
    *,
    realtime_start: str,
    realtime_end: str = LATEST_REALTIME,
    limit: int = OBSERVATIONS_PAGE_LIMIT,
    offset: int = 0,
) -> str:
    """Build the URL for the observations endpoint over a realtime window.

    Sends an explicit ``realtime_start`` / ``realtime_end`` window so FRED
    tags each observation with its true ``realtime_start`` — the vintage
    key under D-013. Two failure modes this navigates between (see the
    G-019/G-027/G-029 history):

    * Omitting the realtime window defaults to *today's* vintage, collapsing
      the vintage-aware table into daily snapshot duplication.
    * The full-history window ``1776-07-04 → 9999-12-31`` returns HTTP 400
      on long daily series — FRED caps full-window JSON at 2000 vintages.

    The collector avoids both by passing a *bounded* window: the daily run
    uses ``realtime_start = last stored vintage`` → ``9999-12-31``, which
    spans only the handful of new vintages since the last collect, far
    under the cap, while ``realtime_end = 9999`` keeps current values'
    vintage windows un-clipped.
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": realtime_start,
        "realtime_end": realtime_end,
        "limit": str(limit),
        "offset": str(offset),
    }
    return f"{FRED_BASE_URL}/series/observations?{urlencode(params)}"


def _redact_key(url: str, api_key: str) -> str:
    """Replace the API key in a URL with ``***`` so raw_blobs.url is safe to log."""
    return url.replace(api_key, "***") if api_key else url


def _as_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def latest_stored_vintage(series_id: str) -> str | None:
    """Most recent stored vintage (``realtime_start``) for a series, ISO string.

    Historical FRED vintages are immutable under the D-013
    ``(series_id, ts, realtime_start)`` schema, so the daily collector only
    needs vintages strictly newer than this. Re-fetching the full vintage
    history every run is what made the collector hang past its 15-min
    workflow timeout (2026-05-16 → 2026-06-19: ~5,000 vintages per daily
    series, killed mid-run, normalize starved for 34 days). Returns ``None``
    when the series has no stored rows yet (→ full fetch, like ``--backfill``).
    """
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT max(realtime_start)::text FROM fred.observations WHERE series_id = %s",
            [series_id],
        )
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    api_key: str | None = None,
    backfill: bool = False,
) -> int:
    """Run the FRED collector once and return the meta.ingest_runs id.

    `api_key` is injectable for testing; production reads ``FRED_API_KEY``.
    By default the collector is **incremental** — for each series it fetches
    only vintages newer than the latest already in ``fred.observations``.
    Pass ``backfill=True`` to re-pull every vintage's full history.
    """
    series = load_series(config_path)
    key = api_key or require_api_key()

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "series_count": len(series),
                "mode": "backfill" if backfill else "incremental",
            },
        ) as run:
            written = 0
            for target_index, target in enumerate(series, start=1):
                since_vintage = None if backfill else latest_stored_vintage(target.series_id)
                written += _fetch_series_pair(
                    target, key, http, run.id, failures, since_vintage=since_vintage
                )
                if target_index % 5 == 0:
                    LOGGER.info("FRED collect progress: %s/%s", target_index, len(series))
            run.add_rows(written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"FRED fetch failed for {len(failures)} endpoint(s); "
                    "no partial macro snapshot will be normalized."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_series_pair(
    target: SeriesTarget,
    api_key: str,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
    *,
    since_vintage: str | None = None,
) -> int:
    """Fetch metadata + observations for one series. Returns 0/1/2 rows written.

    ``since_vintage`` (ISO ``YYYY-MM-DD``) sets the observations
    ``realtime_start`` so only that vintage forward is fetched; already-stored
    historical vintages are skipped, keeping the request well under FRED's
    2000-vintage cap.
    """
    written = 0
    for blob_prefix in (SERIES_BLOB_PREFIX, OBSERVATIONS_BLOB_PREFIX):
        endpoint_name = f"{blob_prefix}{target.series_id}"
        try:
            if blob_prefix == SERIES_BLOB_PREFIX:
                url = build_series_url(api_key, target.series_id)
                payload = http.get_json(url)
            else:
                url, payload = _fetch_observations_payload(
                    target, api_key, http, since_vintage=since_vintage
                )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.HTTPStatusError,
            json.JSONDecodeError,
        ) as exc:
            safe_error = _redact_key(str(exc), api_key)
            LOGGER.warning("FRED fetch failed for %s: %s", endpoint_name, safe_error)
            failures.append(
                {"name": endpoint_name, "url": _redact_key(url, api_key), "error": safe_error}
            )
            continue
        # Persist the redacted URL so raw_blobs.url can't leak the key.
        db.store_raw_blob(ingest_run_id, endpoint_name, _redact_key(url, api_key), payload)
        written += 1
    return written


def _fetch_observations_payload(
    target: SeriesTarget,
    api_key: str,
    http: HttpClient,
    *,
    since_vintage: str | None = None,
) -> tuple[str, Any]:
    """Fetch observations over a bounded realtime window; return one raw payload.

    ``since_vintage`` (ISO ``YYYY-MM-DD``) becomes ``realtime_start`` so only
    that vintage forward is requested — historical vintages are immutable
    under D-013 and already stored, so the daily collector skips them and
    stays far under FRED's 2000-vintage cap. ``None`` (a fresh series or
    ``--backfill``) requests the full history from ``EARLIEST_REALTIME``;
    that can hit the cap on long daily series, so backfilling those is a
    known limitation (they're already populated — incremental carries them
    forward). Paginates on ``count`` like the other collectors.
    """
    realtime_start = since_vintage or EARLIEST_REALTIME
    first_url = build_observations_url(
        api_key,
        target.series_id,
        realtime_start=realtime_start,
        limit=OBSERVATIONS_PAGE_LIMIT,
        offset=0,
    )
    combined: dict[str, Any] | None = None
    observations: list[Any] = []
    offset = 0
    expected_count: int | None = None
    while True:
        url = build_observations_url(
            api_key,
            target.series_id,
            realtime_start=realtime_start,
            limit=OBSERVATIONS_PAGE_LIMIT,
            offset=offset,
        )
        payload = http.get_json(url)
        if not isinstance(payload, dict):
            raise ValueError(
                f"FRED observations payload for {target.series_id} is not an object."
            )
        page_observations = payload.get("observations")
        if not isinstance(page_observations, list):
            raise ValueError(
                f"FRED observations payload for {target.series_id} is missing observations."
            )
        if combined is None:
            combined = dict(payload)
            combined["offset"] = 0
        page_count = _as_non_negative_int(payload.get("count"))
        expected_count = page_count if expected_count is None else expected_count
        observations.extend(page_observations)

        if expected_count is not None and len(observations) >= expected_count:
            break
        if len(page_observations) < OBSERVATIONS_PAGE_LIMIT:
            if expected_count is not None and len(observations) < expected_count:
                raise ValueError(
                    f"FRED observations payload for {target.series_id} ended after "
                    f"{len(observations)} of {expected_count} rows."
                )
            break
        offset += OBSERVATIONS_PAGE_LIMIT

    if combined is None:
        raise ValueError(f"FRED observations payload for {target.series_id} was empty.")
    combined["observations"] = observations
    combined["count"] = len(observations)
    combined["limit"] = len(observations)
    return first_url, combined


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect FRED macro-series snapshots into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_WATCHLIST_PATH)
    parser.add_argument(
        "--backfill",
        action="store_true",
        help=(
            "Re-fetch the full vintage history for every series. Default is "
            "incremental: only vintages newer than what's already stored."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = collect(args.config, backfill=args.backfill)
    print(f"FRED collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
