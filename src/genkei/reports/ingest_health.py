"""Periodic ingest-health summary report (B-053).

A committed, human-readable health snapshot across **every** active source —
staleness, primary-table liveness, and schema drift — so operational issues
surface without anyone running ``genkei watchlist health`` by hand. It is the
*narrative, full-roster* companion to two things that already exist and that
this module deliberately does **not** duplicate:

* **B-119** (CI failure alerts / GitHub issues / Discord + heartbeat) is
  *real-time alerting on failure*. This report is a *periodic digest of the
  whole roster*, healthy sources included — a record, not a pager.
* The **signal digest**'s lake-health footer
  (:mod:`genkei.reports.signal_digest`) lists only the *not-OK* sources as a
  footnote. This report renders every source on its own cadence.

Scope decision (recorded per the mission): this is a small new renderer that
**reuses the existing ``genkei watchlist health`` query layer**
(``_query_source_health`` / ``_with_health_status`` / ``_drift_rows``) rather
than inventing a second definition of "stale" (CLAUDE.md clean-code rule).

Design mirrors the digest: the pure renderer (:func:`render_health_report`)
is split from all DB access (:func:`build_health_report`) so formatting is
unit-testable offline with synthetic health rows.

Run it::

    python -m genkei.reports.ingest_health           # -> reports/health/ingest-health-<today>.md
    python -m genkei.reports.ingest_health --stdout  # print, don't write
    python -m genkei.reports.ingest_health --stale-hours 48

Cadence: weekly (or daily) via a ``/schedule`` routine — the module is
runner-agnostic, so a GH Actions cron works equally well::

    # weekly, Mondays ~07:13 UTC
    13 7 * * 1   python -m genkei.reports.ingest_health

Failed runs alert via the B-119 path (the runner that fires this is itself
watched), so a silent drop is itself caught.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT_DIR = Path("reports/health")
DEFAULT_STALE_HOURS = 36.0


def _run_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if "endpoint" in r and "drift_kind" not in r]


def _table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if "table" in r]


def _drift_only(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("drift_kind")]


def _age_str(age: Any) -> str:
    return f"{float(age):.1f}" if age is not None else "n/a"


def render_health_report(
    health_rows: list[dict[str, Any]] | None,
    *,
    generated_at: datetime,
    stale_hours: float = DEFAULT_STALE_HOURS,
) -> str:
    """Render the ingest-health report markdown. Pure — no DB or clock access.

    ``health_rows`` is the tagged output of the ``watchlist health`` query
    layer: a mix of ingest-run rows (``source`` / ``endpoint`` /
    ``last_started_at`` / ``age_hours`` / ``health_status``), primary-table
    liveness rows (``source`` / ``table`` / ``has_rows``), and schema-drift
    rows (``drift_kind`` / ``detail``). Every source appears — OK and not.
    """
    gen = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = []
    lines.append("# Genkei Capital — Ingest Health Report")
    lines.append("")
    lines.append(f"**Generated:** {gen} · **Stale cutoff:** {stale_hours:.0f}h")
    lines.append("")
    lines.append(
        "Full-roster snapshot of every active ingest source — staleness, "
        "primary-table liveness, and schema drift. This is the periodic "
        "record; real-time failure paging is B-119 (CI alerts / Discord / "
        "heartbeat). Mirror of `genkei watchlist health` as a committed "
        "artifact."
    )
    lines.append("")

    if not health_rows:
        lines.append("_Health snapshot unavailable — the lake was unreachable "
                     "at generation time._")
        lines.append("")
        return "\n".join(lines) + "\n"

    runs = _run_rows(health_rows)
    tables = _table_rows(health_rows)
    drift = _drift_only(health_rows)

    # ---- Summary -----------------------------------------------------------
    run_not_ok = [r for r in runs if r.get("health_status") not in (None, "OK")]
    table_not_ok = [r for r in tables if r.get("health_status") not in (None, "OK")]
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"- **{len(runs)}** ingest endpoint(s): "
        f"{len(runs) - len(run_not_ok)} OK · {len(run_not_ok)} need attention."
    )
    lines.append(
        f"- **{len(tables)}** primary table(s): "
        f"{len(tables) - len(table_not_ok)} live · {len(table_not_ok)} empty/failing."
    )
    lines.append(
        f"- **{len(drift)}** schema-drift finding(s)."
        if drift
        else "- No schema drift detected."
    )
    if run_not_ok or table_not_ok or drift:
        lines.append(
            "- ⚠ Action needed — see flagged rows below; cross-check with "
            "`meta.ingest_runs` via the timestamps."
        )
    else:
        lines.append("- ✅ All sources healthy at generation.")
    lines.append("")

    # ---- Ingest runs (every endpoint) --------------------------------------
    lines.append("## Ingest runs")
    lines.append("")
    lines.append("| source | endpoint | status | age (h) | last run (UTC) | note |")
    lines.append("|---|---|---|---|---|---|")
    for r in sorted(runs, key=lambda r: (r.get("source", ""), r.get("endpoint", ""))):
        last = r.get("last_started_at") or "—"
        note = (r.get("error") or "")[:60]
        lines.append(
            f"| {r.get('source','?')} | {r.get('endpoint','?')} | "
            f"{r.get('health_status','?')} | {_age_str(r.get('age_hours'))} | "
            f"{last} | {note} |"
        )
    lines.append("")

    # ---- Primary-table liveness --------------------------------------------
    lines.append("## Primary-table liveness")
    lines.append("")
    lines.append("| source | table | status | data |")
    lines.append("|---|---|---|---|")
    for r in sorted(tables, key=lambda r: (r.get("source", ""), r.get("table", ""))):
        has = r.get("has_rows")
        data = "yes" if has else "no" if has is not None else "—"
        lines.append(
            f"| {r.get('source','?')} | {r.get('table','?')} | "
            f"{r.get('health_status','?')} | {data} |"
        )
    lines.append("")

    # ---- Schema drift ------------------------------------------------------
    lines.append("## Schema drift (B-072)")
    lines.append("")
    if not drift:
        lines.append("None detected across recent `meta.raw_blobs`. ✓")
    else:
        lines.append("| source | endpoint_kind | kind | detail |")
        lines.append("|---|---|---|---|")
        for r in drift:
            sample = r.get("sample_endpoint_name")
            detail = r.get("detail", "")
            if sample:
                detail = f"({sample}) {detail}"
            lines.append(
                f"| {r.get('source','?')} | {r.get('endpoint_kind','?')} | "
                f"{r.get('drift_kind','?')} | {detail[:100]} |"
            )
    lines.append("")

    # ---- Footer ------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append(
        "_Status tags: OK · STALE (no successful run within the cutoff) · "
        "FAIL · MISSING (recurring endpoint never ran) · EMPTY (table has no "
        "rows) · DRIFT. Anomalies are traceable via the last-run timestamps "
        "against `meta.ingest_runs`. Generated by "
        "`python -m genkei.reports.ingest_health` (B-053)._"
    )
    return "\n".join(lines) + "\n"


def build_health_report(
    *, stale_hours: float = DEFAULT_STALE_HOURS, drift_max_age_hours: int = 72
) -> tuple[str, date]:
    """Fetch the ``watchlist health`` roster from Postgres and render the report.

    Returns ``(markdown, generated_date)``. The DB-touching counterpart to
    :func:`render_health_report`; imports live here so renderer unit tests
    don't require psycopg. Reuses the ``watchlist health`` query layer so the
    definition of "stale"/"drift" stays in one place.
    """
    from genkei.cli.watchlist import (
        _drift_rows,
        _query_source_health,
        _with_health_status,
    )

    generated_at = datetime.now(timezone.utc)
    rows = _query_source_health() + _drift_rows(max_age_hours=drift_max_age_hours)
    health_rows = _with_health_status(rows, stale_hours=stale_hours)
    markdown = render_health_report(
        health_rows, generated_at=generated_at, stale_hours=stale_hours
    )
    return markdown, generated_at.date()


def write_report(markdown: str, day: date, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    """Write the report to ``<output_dir>/ingest-health-<day>.md`` (idempotent)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"ingest-health-{day.isoformat()}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the periodic ingest-health report into reports/health/."
    )
    parser.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    parser.add_argument("--drift-max-age-hours", type=int, default=72)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the report to stdout instead of writing a file.",
    )
    args = parser.parse_args(argv)
    if args.stale_hours <= 0:
        parser.error("--stale-hours must be a positive number")
    return args


def main(argv: list[str] | None = None) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    markdown, day = build_health_report(
        stale_hours=args.stale_hours, drift_max_age_hours=args.drift_max_age_hours
    )
    if args.stdout:
        print(markdown)
    else:
        path = write_report(markdown, day, output_dir=args.output_dir)
        print(f"Wrote ingest-health report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
