"""BEA normalizer — reads meta.raw_blobs, upserts bea.* (B-029).

A normalizer run is itself a row in ``meta.ingest_runs`` with
``endpoint='normalize'`` and ``metadata.source_run_id`` pointing at the
collector run whose blobs were processed (same audit pattern as FRED's
B-028 normalizer).

**Watchlist filter at parse time** — each BEA table response contains
every line on the table (~30-100 rows per table). The watchlist (per
B-029's design call) curates 10 specific lines across 6 tables; the
normalizer reads the watchlist + filters the parse output down to the
watched ``(table_id, line_number)`` pairs. Lines outside the watchlist
are dropped without warning — they're useful for future watchlist
expansions but pollute the lake at the v1 scope.

**Vintage** — latest-only (NOT vintage-aware). ``bea.observations`` PK
is ``(series_id, ts, frequency)`` with no vintage column; re-running
this normalizer upserts new values over old ones, matching BEA's own
revise-in-place semantics. See the migration docstring for the v2
vintage-aware path.

**TimePeriod parsing** — BEA returns the period as a string:
``"2024Q1"`` for quarterly, ``"2024"`` for annual, ``"2024M03"`` for
monthly. The parser normalises to the period-start UTC timestamp
(2024-Q1 → 2024-01-01, 2024 → 2024-01-01, 2024M03 → 2024-03-01) so the
hypertable's ``ts`` column always points at the start of the reporting
window.

**Missing-value sentinel** — BEA returns ``"..."`` (three dots) for
withheld values and may return raw thousands-separated numbers
(``"23,128.3"``). Both are handled by ``parse_bea_value`` returning
``None`` / parsed float respectively.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from typing import Any

from genkei.common import db
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist

SOURCE_NAME = "bea"
NORMALIZE_ENDPOINT_LABEL = "normalize"
COLLECT_ENDPOINT_LABEL = "collect"
BLOB_PREFIX = "bea_"
RawBlob = tuple[str, Any, datetime]
JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_bea_value(raw: Any) -> float | None:
    """Coerce a BEA DataValue field to ``float``; ``"..."`` means missing.

    BEA also publishes thousands-separated numbers as strings
    (``"23,128.3"``); strip commas before the float conversion. The
    rare empty string also collapses to None rather than raising.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped or stripped in ("...", "."):
            return None
        cleaned = stripped.replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def parse_time_period(raw: Any) -> tuple[datetime, str] | None:
    """Parse BEA TimePeriod into (UTC period-start, frequency).

    Returns None for unparseable inputs. Supports the three documented
    BEA formats:
      - ``"2024Q1"``..``"2024Q4"`` → quarterly, ts = period-start
      - ``"2024M01"``..``"2024M12"`` → monthly, ts = month-start
      - ``"2024"`` → annual, ts = January 1
    """
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    # Quarterly: "2024Q1"
    if "Q" in stripped:
        year_part, _, quarter_part = stripped.partition("Q")
        if year_part.isdigit() and quarter_part.isdigit():
            year = int(year_part)
            quarter = int(quarter_part)
            if 1 <= quarter <= 4:
                month = (quarter - 1) * 3 + 1
                return _ts(date(year, month, 1)), "Q"
        return None
    # Monthly: "2024M03"
    if "M" in stripped:
        year_part, _, month_part = stripped.partition("M")
        if year_part.isdigit() and month_part.isdigit():
            year = int(year_part)
            month = int(month_part)
            if 1 <= month <= 12:
                return _ts(date(year, month, 1)), "M"
        return None
    # Annual: "2024"
    if stripped.isdigit() and len(stripped) == 4:
        year = int(stripped)
        return _ts(date(year, 1, 1)), "A"
    return None


def _ts(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Per-table normalizer
# ---------------------------------------------------------------------------


def normalize_table(
    payload: Any,
    *,
    table_id: str,
    frequency: str,
    watched_lines: set[int],
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> tuple[list[JsonObject], list[JsonObject]]:
    """Parse one BEA table-response blob into (series_rows, observation_rows).

    Returns rows for the *watched* lines only — every other line on the
    BEA table is dropped silently. Per-line dedup: if BEA's response
    accidentally contains duplicate rows for the same (line_number,
    TimePeriod), we keep the last one seen (BEA does this rarely but
    the parser stays defensive).
    """
    if not isinstance(payload, dict):
        return [], []
    bea = payload.get("BEAAPI")
    if not isinstance(bea, dict):
        return [], []
    results = bea.get("Results")
    if not isinstance(results, dict):
        return [], []
    data = results.get("Data")
    if not isinstance(data, list):
        return [], []

    # Build per-line series + per-(line, ts) observation rows.
    series_by_line: dict[int, JsonObject] = {}
    observations_by_key: dict[tuple[int, datetime], JsonObject] = {}

    for row in data:
        if not isinstance(row, dict):
            continue
        line_raw = row.get("LineNumber")
        try:
            line_number = int(line_raw)
        except (TypeError, ValueError):
            continue
        if line_number not in watched_lines:
            continue

        period = parse_time_period(row.get("TimePeriod"))
        if period is None:
            continue
        ts, period_frequency = period
        # The blob's frequency MUST match the parsed-period frequency
        # — otherwise the collector queried 'Q' and BEA returned an 'A'
        # row, which shouldn't happen but the assertion protects the
        # PK invariant.
        if period_frequency != frequency:
            LOGGER.warning(
                "BEA row TimePeriod %r parsed as %s; blob requested %s — dropping",
                row.get("TimePeriod"),
                period_frequency,
                frequency,
            )
            continue

        series_id = f"{table_id}:{line_number}"

        # series row (one per line, last-seen wins for metadata fields).
        # All rows in a single table-response share the line's metadata
        # so the last-wins is benign.
        series_by_line[line_number] = {
            "series_id": series_id,
            "table_id": table_id,
            "line_number": line_number,
            "line_description": _stringify(row.get("LineDescription")),
            "series_code": _stringify(row.get("SeriesCode")),
            "units": _stringify(row.get("CL_UNIT") or row.get("METRIC_NAME")),
            "frequency": frequency,
            "note_refs": _parse_note_refs(row.get("NoteRef")),
            "source_endpoint": source_endpoint,
            "fetched_at": fetched_at,
            "ingest_run_id": ingest_run_id,
        }

        observations_by_key[(line_number, ts)] = {
            "series_id": series_id,
            "ts": ts,
            "frequency": frequency,
            "value": parse_bea_value(row.get("DataValue")),
            "source_endpoint": source_endpoint,
            "fetched_at": fetched_at,
            "ingest_run_id": ingest_run_id,
        }

    return list(series_by_line.values()), list(observations_by_key.values())


def _parse_note_refs(raw: Any) -> list[str]:
    """Split BEA's comma-separated NoteRef string into a stable list."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [n.strip() for n in raw.split(",") if n.strip()]


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def latest_collector_run_id() -> int:
    """Return the most recent successful BEA collector run id."""
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
            "No successful BEA collector run found in meta.ingest_runs. "
            "Run `python -m genkei.ingest.bea` first."
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
        name: (url, payload, fetched_at)
        for name, url, payload, fetched_at in rows
    }


def _build_watched_lines_index(config_path) -> dict[tuple[str, str], set[int]]:
    """Map ``(table_id_lower, frequency_lower)`` → set of watched line numbers.

    Keys mirror the collector's ``blob_endpoint`` shape
    (``bea_<table_lower>_<frequency_lower>``) so the dispatch loop can
    look up the filter set in O(1) per blob.
    """
    watchlist = load_watchlist(config_path)
    out: dict[tuple[str, str], set[int]] = {}
    for entry in watchlist.bea:
        key = (entry.table_id.lower(), entry.frequency.lower())
        out.setdefault(key, set()).add(entry.line_number)
    return out


def normalize(
    *,
    source_run_id: int | None = None,
    config_path=DEFAULT_WATCHLIST_PATH,
) -> int:
    """Run the BEA normalizer once and return the normalizer run id."""
    if source_run_id is None:
        source_run_id = latest_collector_run_id()
    blobs = fetch_raw_blobs(source_run_id)
    watched_index = _build_watched_lines_index(config_path)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        series_rows: list[JsonObject] = []
        observation_rows: list[JsonObject] = []

        for endpoint_name, (url, payload, fetched_at) in blobs.items():
            if not endpoint_name.startswith(BLOB_PREFIX):
                continue
            # bea_t10101_q → ("t10101", "q")
            stem = endpoint_name[len(BLOB_PREFIX) :]
            try:
                table_part, freq_part = stem.rsplit("_", 1)
            except ValueError:
                LOGGER.debug(
                    "BEA normalizer skipping malformed blob name: %s",
                    endpoint_name,
                )
                continue
            watched = watched_index.get((table_part, freq_part))
            if not watched:
                LOGGER.debug(
                    "BEA normalizer skipping unwatched blob: %s", endpoint_name
                )
                continue
            table_rows, obs_rows = normalize_table(
                payload,
                table_id=table_part.upper(),
                frequency=freq_part.upper(),
                watched_lines=watched,
                source_endpoint=url,
                ingest_run_id=run.id,
                fetched_at=fetched_at,
            )
            series_rows.extend(table_rows)
            observation_rows.extend(obs_rows)

        # Dedup series rows across blobs — the same line_number could
        # appear in multiple table responses if a watchlist accidentally
        # listed it twice; last-write-wins on series metadata.
        series_dedup: dict[str, JsonObject] = {
            row["series_id"]: row for row in series_rows
        }

        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "bea.series",
                    list(series_dedup.values()),
                    conflict_keys=["series_id"],
                )
            )
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "bea.observations",
                    observation_rows,
                    conflict_keys=["series_id", "ts", "frequency"],
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
        help="Watchlist path (drives the line-filter).",
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
        from pathlib import Path as _Path

        run_id = normalize(
            source_run_id=args.source_run_id,
            config_path=_Path(args.config),
        )
    except SystemExit:
        raise
    except Exception as exc:
        LOGGER.exception("BEA normalize failed")
        if args.json:
            import json as json_mod

            print(json_mod.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"BEA normalize failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        import json as json_mod

        print(json_mod.dumps({"ok": True, "normalize_run_id": run_id}))
    else:
        print(f"BEA normalize: meta.ingest_runs id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
