"""FRED macro-series collector (B-028).

Fetches series metadata + full-vintage observations for every series
configured under ``macro_series:`` in ``config/watchlists.yml``. Lands
two raw blobs per series (``series_<id>``, ``observations_<id>``) in
``meta.raw_blobs``. The downstream normalizer
(``genkei.normalize.fred``) reads from those blobs.

Single-mode design (D-014): FRED's ``/series/observations`` returns the
full series in one call, so daily and backfill flows are the same code
path. Each daily run upserts the latest state of every observation
including any new vintages — D-013's vintage-aware schema means new
revisions land as new rows rather than overwriting historical values.

Vintage fetch: every observations call uses
``realtime_start=1776-07-04&realtime_end=9999-12-31`` so FRED returns
every vintage of every observation — newer revisions get fresh
``realtime_start`` values, older values stay current until superseded.

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

import yaml

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

DEFAULT_WATCHLIST_PATH = Path("config/watchlists.yml")
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
RAW_BLOBS_INSERT = (
    "INSERT INTO meta.raw_blobs (ingest_run_id, endpoint_name, url, payload) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (ingest_run_id, endpoint_name) DO NOTHING"
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeriesTarget:
    """A FRED series we want to fetch."""

    series_id: str
    name: str
    rationale: str | None = None


def load_series(path: Path) -> list[SeriesTarget]:
    """Read ``macro_series:`` from watchlists.yml as ``SeriesTarget``s."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise SystemExit(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Watchlist root must be a YAML mapping.")
    raw = data.get("macro_series", [])
    if not isinstance(raw, list) or not raw:
        raise SystemExit("watchlists.yml is missing macro_series or it is empty.")
    out: list[SeriesTarget] = []
    seen_ids: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise SystemExit("Each macro_series entry must be a mapping.")
        sid = entry.get("id")
        name = entry.get("name")
        if not isinstance(sid, str) or not sid:
            raise SystemExit("macro_series entry is missing a string `id`.")
        if sid in seen_ids:
            raise SystemExit(f"Duplicate macro_series id: {sid}")
        seen_ids.add(sid)
        if not isinstance(name, str) or not name:
            raise SystemExit(f"macro_series entry {sid!r} is missing a string `name`.")
        rationale = entry.get("rationale")
        out.append(
            SeriesTarget(
                series_id=sid,
                name=name,
                rationale=rationale if isinstance(rationale, str) else None,
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


def build_observations_url(api_key: str, series_id: str) -> str:
    """Build the URL for the full-vintage observations endpoint."""
    return (
        f"{FRED_BASE_URL}/series/observations"
        f"?series_id={series_id}"
        f"&api_key={api_key}"
        f"&file_type=json"
        f"&realtime_start={EARLIEST_REALTIME}"
        f"&realtime_end=9999-12-31"
    )


def _store_blob(ingest_run_id: int, endpoint_name: str, url: str, payload: Any) -> None:
    """Insert one raw_blobs row, redacting the API key from the stored URL."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(RAW_BLOBS_INSERT, [ingest_run_id, endpoint_name, url, json.dumps(payload)])


def _redact_key(url: str, api_key: str) -> str:
    """Replace the API key in a URL with ``***`` so raw_blobs.url is safe to log."""
    return url.replace(api_key, "***") if api_key else url


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    api_key: str | None = None,
) -> int:
    """Run the FRED collector once and return the meta.ingest_runs id.

    `api_key` is injectable for testing; production reads ``FRED_API_KEY``.
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
            metadata={"watchlist_path": str(config_path), "series_count": len(series)},
        ) as run:
            written = 0
            for target_index, target in enumerate(series, start=1):
                written += _fetch_series_pair(target, key, http, run.id, failures)
                if target_index % 5 == 0:
                    LOGGER.info("FRED collect progress: %s/%s", target_index, len(series))
            run.add_rows(written)
            if failures:
                _record_partial(run.id, failures)
            if written == 0 and failures:
                raise RuntimeError("All FRED fetches failed; no raw blobs written.")
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
) -> int:
    """Fetch metadata + observations for one series. Returns 0/1/2 rows written."""
    written = 0
    for blob_prefix, url_builder in (
        (SERIES_BLOB_PREFIX, build_series_url),
        (OBSERVATIONS_BLOB_PREFIX, build_observations_url),
    ):
        url = url_builder(api_key, target.series_id)
        endpoint_name = f"{blob_prefix}{target.series_id}"
        try:
            payload = http.get_json(url)
        except Exception as exc:
            safe_error = _redact_key(str(exc), api_key)
            LOGGER.warning("FRED fetch failed for %s: %s", endpoint_name, safe_error)
            failures.append(
                {"name": endpoint_name, "url": _redact_key(url, api_key), "error": safe_error}
            )
            continue
        # Persist the redacted URL so raw_blobs.url can't leak the key.
        _store_blob(ingest_run_id, endpoint_name, _redact_key(url, api_key), payload)
        written += 1
    return written


def _record_partial(ingest_run_id: int, partial: list[dict[str, str]]) -> None:
    """Stash per-series partial-failure metadata on the ingest_runs row."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE meta.ingest_runs SET metadata = "
            "COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('partial_endpoints', %s::jsonb) "
            "WHERE id = %s",
            [json.dumps(partial), ingest_run_id],
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect FRED macro-series snapshots into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_WATCHLIST_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = collect(args.config)
    print(f"FRED collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
