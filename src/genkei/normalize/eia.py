"""EIA Open Data v2 normalizer — reads meta.raw_blobs, upserts eia.* (B-032).

A normalizer run is itself a row in ``meta.ingest_runs`` with
``endpoint='normalize'`` and ``metadata.source_run_id`` pointing at the
collector run whose blobs were processed (same audit pattern as FRED's
B-028, BEA's B-029, Treasury's B-030 normalizers).

**Per-blob dispatch** — each blob's ``endpoint_name`` (e.g.
``eia_wti_spot``) is a slugified ``series_id`` that reverse-maps to
its watchlist entry. The normalizer indexes watchlist series by their
slug, picks each blob's series, and projects ``response.data`` rows
into ``(series_id, ts, value)`` observations.

**Date parsing** — EIA's ``period`` field varies by frequency:
``YYYY-MM-DD`` for daily, ``YYYY-MM-DD`` (week-ending Friday) for
weekly, ``YYYY-MM`` for monthly, ``YYYY-Qn`` for quarterly,
``YYYY`` for annual. Each is parsed to a UTC midnight at the period's
canonical start so the hypertable's ``ts`` always points at the
beginning of the reporting period.

**Numeric values** — EIA returns numbers as native floats / ints in
most routes but occasionally as strings (the electricity operational
data endpoint). ``parse_value`` handles both plus the standard
"empty / null / N/A" sentinels.

**Vintage** — latest-only (NOT vintage-aware). ``eia.observations`` PK
is ``(series_id, ts)``; re-running this normalizer upserts new values
over old ones, matching EIA's revise-in-place semantics.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from genkei.common import db
from genkei.common.slugs import blob_slug_part as _blob_slug_part
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EiaSeriesEntry,
    load_watchlist,
)
from genkei.ingest.eia import BLOB_PREFIX

SOURCE_NAME = "eia"
NORMALIZE_ENDPOINT_LABEL = "normalize"
COLLECT_ENDPOINT_LABEL = "collect"
RawBlob = tuple[str, Any, datetime]
JsonObject = dict[str, Any]
SourceRunRow = tuple[Any, Any, Any, Any]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_value(raw: Any) -> float | None:
    """Coerce an EIA Open Data field to ``float``.

    EIA serializes numerics as native ints / floats in most routes,
    occasionally as strings (electricity operational data). Booleans,
    ``None``, the empty string, and ``"null"`` / ``"NA"`` sentinels
    collapse to ``None`` rather than raising.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped or stripped.lower() in ("null", "n/a", "na", "-"):
            return None
        cleaned = stripped.replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def parse_period(raw: Any, *, frequency: str) -> datetime | None:
    """Parse an EIA ``period`` value into a UTC midnight datetime.

    Period shapes by frequency:
      * ``D`` — ``YYYY-MM-DD`` (observation date)
      * ``W`` — ``YYYY-MM-DD`` (week-ending Friday)
      * ``M`` — ``YYYY-MM`` (month-start)
      * ``Q`` — ``YYYY-Qn`` (quarter-start)
      * ``A`` — ``YYYY`` (year-start)

    Returns ``None`` for unparseable inputs. Already-typed dates are
    accepted as a fallback for non-standard payload shapes.
    """
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)
    if isinstance(raw, date):
        return _ts(raw)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    freq = frequency.upper()
    try:
        if freq in ("D", "W"):
            parsed = datetime.strptime(stripped, "%Y-%m-%d").date()
            return _ts(parsed)
        if freq == "M":
            parsed = datetime.strptime(stripped, "%Y-%m").date()
            return _ts(parsed)
        if freq == "Q":
            year_str, _, q_str = stripped.partition("-Q")
            if not year_str or not q_str:
                return None
            year = int(year_str)
            quarter = int(q_str)
            if quarter < 1 or quarter > 4:
                return None
            month = (quarter - 1) * 3 + 1
            return _ts(date(year, month, 1))
        if freq == "A":
            year = int(stripped)
            return _ts(date(year, 1, 1))
    except ValueError:
        return None
    return None


def _ts(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Per-series normalizer
# ---------------------------------------------------------------------------


def normalize_series(
    payload: Any,
    *,
    entry: EiaSeriesEntry,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> tuple[JsonObject, list[JsonObject]]:
    """Parse one EIA series blob into (series_row, observation_rows).

    Within a blob, EIA may include multiple facet projections if the
    request didn't pin every facet — defensively we filter rows by the
    watchlist entry's ``facets`` so cross-projection rows can't leak in.
    Within the watched projection, the last value per period wins (EIA
    occasionally publishes intra-period revisions in the same payload).
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"EIA payload for series {entry.series_id} is not a JSON object."
        )
    response_block = payload.get("response")
    if not isinstance(response_block, dict):
        raise ValueError(
            f"EIA payload for series {entry.series_id} is missing a `response` object."
        )
    data = response_block.get("data")
    if not isinstance(data, list):
        raise ValueError(
            f"EIA payload for series {entry.series_id} has invalid/missing "
            "`response.data` list."
        )

    observations_by_ts: dict[datetime, JsonObject] = {}
    matched_rows = 0

    for raw_row in data:
        if not isinstance(raw_row, dict):
            continue
        if not _row_matches_facets(raw_row, entry.facets):
            continue
        if entry.data_field not in raw_row:
            raise ValueError(
                f"EIA response for series {entry.series_id} is missing data field "
                f"{entry.data_field!r} (route {entry.route})."
            )
        raw_period = raw_row.get(entry.date_field)
        ts = parse_period(raw_period, frequency=entry.frequency)
        if ts is None:
            raise ValueError(
                f"EIA response for series {entry.series_id} has invalid "
                f"{entry.date_field!r} value {raw_period!r} (route {entry.route})."
            )
        matched_rows += 1
        observations_by_ts[ts] = {
            "series_id": entry.series_id,
            "ts": ts,
            "value": parse_value(raw_row[entry.data_field]),
            "source_endpoint": source_endpoint,
            "fetched_at": fetched_at,
            "ingest_run_id": ingest_run_id,
        }

    if matched_rows == 0:
        raise ValueError(
            f"EIA response for series {entry.series_id} (route {entry.route}) "
            f"matched no rows under facets {dict(entry.facets)!r}."
        )

    series_row: JsonObject = {
        "series_id": entry.series_id,
        "name": entry.name,
        "route": entry.route,
        "data_field": entry.data_field,
        "date_field": entry.date_field,
        "facets": Jsonb(dict(entry.facets)) if entry.facets else None,
        "units": entry.units,
        "frequency": entry.frequency,
        "rationale": entry.rationale,
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }
    return series_row, list(observations_by_ts.values())


def _row_matches_facets(row: Mapping[str, Any], facets: Mapping[str, str]) -> bool:
    """True if every facet in ``facets`` matches the row case-sensitively.

    EIA returns facet columns alongside data columns. ``str()`` coerces
    occasional numeric facet values into a comparable form.
    """
    if not facets:
        return True
    for key, expected in facets.items():
        actual = row.get(key)
        if actual is None or str(actual) != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def latest_collector_run_id() -> int:
    """Return the most recent successful EIA collector run id."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM meta.ingest_runs "
            "WHERE source = %s AND endpoint = %s AND status = 'success' "
            "ORDER BY started_at DESC LIMIT 1",
            [SOURCE_NAME, COLLECT_ENDPOINT_LABEL],
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(
            "No successful EIA collector run found in meta.ingest_runs. "
            "Run `python -m genkei.ingest.eia` first."
        )
    return int(row[0])


def fetch_raw_blobs(source_run_id: int) -> dict[str, RawBlob]:
    """Return ``{endpoint_name: (url, payload, fetched_at)}`` for a run."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT endpoint_name, url, payload, fetched_at "
            "FROM meta.raw_blobs WHERE ingest_run_id = %s",
            [source_run_id],
        )
        rows = cur.fetchall()
    return {
        name: (url, payload, fetched_at) for name, url, payload, fetched_at in rows
    }


def validate_source_run(source_run_id: int) -> None:
    """Fail unless ``source_run_id`` points at a complete EIA collect run."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source, endpoint, status, metadata "
            "FROM meta.ingest_runs WHERE id = %s",
            [source_run_id],
        )
        row = cur.fetchone()
    _validate_source_run_row(source_run_id, row)


def _validate_source_run_row(
    source_run_id: int,
    row: SourceRunRow | None,
) -> None:
    if row is None:
        raise SystemExit(
            f"No EIA collector run found for ingest_run_id={source_run_id}."
        )
    source, endpoint, status, metadata = row
    if source != SOURCE_NAME or endpoint != COLLECT_ENDPOINT_LABEL:
        raise SystemExit(
            f"ingest_run_id={source_run_id} is not an EIA collect run "
            f"(source={source!r}, endpoint={endpoint!r})."
        )
    if status != "success":
        raise SystemExit(
            f"EIA source run {source_run_id} is not successful (status={status!r})."
        )
    partial_names = _partial_endpoint_names(metadata)
    if partial_names:
        names = ", ".join(partial_names)
        raise SystemExit(
            f"EIA source run {source_run_id} has partial endpoint failure(s): "
            f"{names}"
        )


def _partial_endpoint_names(metadata: Any) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    partial = metadata.get("partial_endpoints")
    if not isinstance(partial, list):
        return []
    names: list[str] = []
    for item in partial:
        if isinstance(item, dict):
            name = item.get("name")
            names.append(str(name) if name else "<unknown>")
        else:
            names.append(str(item))
    return names


def _series_by_blob_name(
    config_path: Path | str,
) -> dict[str, EiaSeriesEntry]:
    """Index watchlist EIA series by their canonical blob endpoint slug."""
    resolved = config_path if isinstance(config_path, Path) else Path(config_path)
    watchlist = load_watchlist(resolved)
    return {_series_blob_name(entry.series_id): entry for entry in watchlist.eia}


def _series_blob_name(series_id: str) -> str:
    """Mirror ``SeriesTarget.blob_endpoint`` slug logic."""
    return f"{BLOB_PREFIX}{_blob_slug_part(series_id)}"


def _validate_blob_coverage(
    source_run_id: int,
    blobs: Mapping[str, RawBlob],
    expected: set[str],
) -> None:
    missing = expected - set(blobs)
    if missing:
        names = ", ".join(sorted(missing))
        raise SystemExit(
            f"EIA source run {source_run_id} missing raw blob endpoint(s): "
            f"{names}"
        )


def normalize(
    *,
    source_run_id: int | None = None,
    config_path: Path | str = DEFAULT_WATCHLIST_PATH,
) -> int:
    """Run the EIA normalizer once and return the normalizer run id."""
    if source_run_id is None:
        source_run_id = latest_collector_run_id()
    validate_source_run(source_run_id)
    series_by_blob = _series_by_blob_name(config_path)
    if not series_by_blob:
        raise SystemExit(
            "watchlists.yml is missing an `eia:` section or it is empty."
        )
    expected_blob_names = set(series_by_blob)
    blobs = fetch_raw_blobs(source_run_id)
    _validate_blob_coverage(source_run_id, blobs, expected_blob_names)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        series_rows: list[JsonObject] = []
        observation_rows: list[JsonObject] = []

        for endpoint_name, (url, payload, fetched_at) in blobs.items():
            entry = series_by_blob.get(endpoint_name)
            if entry is None:
                LOGGER.debug(
                    "EIA normalizer skipping unrecognized blob: %s",
                    endpoint_name,
                )
                continue
            series_row, observations = normalize_series(
                payload,
                entry=entry,
                source_endpoint=url,
                ingest_run_id=run.id,
                fetched_at=fetched_at,
            )
            series_rows.append(series_row)
            observation_rows.extend(observations)

        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "eia.series",
                    series_rows,
                    conflict_keys=["series_id"],
                )
            )
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "eia.observations",
                    observation_rows,
                    conflict_keys=["series_id", "ts"],
                )
            )
        return run.id


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-run-id",
        type=int,
        help="Specific collector run to normalize (default: latest success).",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_WATCHLIST_PATH),
        help="Watchlist path (drives the per-blob series lookup).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit summary as JSON for agent consumption.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        run_id = normalize(
            source_run_id=args.source_run_id,
            config_path=Path(args.config),
        )
    except SystemExit:
        raise
    except Exception as exc:
        LOGGER.exception("EIA normalize failed")
        if args.json:
            import json as json_mod

            print(json_mod.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"EIA normalize failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        import json as json_mod

        print(json_mod.dumps({"ok": True, "normalize_run_id": run_id}))
    else:
        print(f"EIA normalize: meta.ingest_runs id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
