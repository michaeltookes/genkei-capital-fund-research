"""``genkei watchlist`` — watchlist coverage & data-lake health (B-044).

Three subcommands:

* **list** — dump the watchlist by sleeve (crypto / equities / macro).
* **health** — per-source ingest health: latest collect + normalize
  status, run counts, primary-table liveness. This is the command
  that should have existed when 3/4 sources went dark silently for ~4
  days (G-027/G-028/D-020). Built loud: any source with a recent
  failure or a stale primary table prints a clear FAIL or STALE tag.
* **gaps** — per-asset freshness: when was the most recent data point
  for each watchlist asset, and how many hours ago was that. Surfaces
  individual assets that have fallen behind even when the source as a
  whole is "healthy".

Usage:
  genkei watchlist list
  genkei watchlist list --sleeve crypto
  genkei watchlist health
  genkei watchlist health --json
  genkei watchlist gaps
  genkei watchlist gaps --threshold-hours 48
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from psycopg import sql

from genkei.cli._watchlist import (
    DEFAULT_WATCHLIST_PATH,
    Watchlist,
    load_watchlist,
)
from genkei.common import db

# Subcommand group — registered into the top-level Typer app via
# ``app.add_typer(watchlist.app, name="watchlist")`` per D-019.
app = typer.Typer(
    name="watchlist",
    help="Watchlist coverage + data-lake health.",
    no_args_is_help=True,
    add_completion=False,
)


def _load_or_exit(config: Path) -> Watchlist:
    try:
        return load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


# ---------------------------------------------------------------------------
# `genkei watchlist list`
# ---------------------------------------------------------------------------


def _format_list_human(wl: Watchlist, *, sleeve: Optional[str]) -> str:
    sections: list[str] = []
    if sleeve in (None, "crypto"):
        sections.append(f"crypto ({len(wl.crypto)})")
        sections.append("-" * len(sections[-1]))
        for c in wl.crypto:
            sections.append(f"  {c.symbol:<8} {c.tier:<10} {c.coingecko_id:<20} {c.name}")
    if sleeve in (None, "equity", "equities"):
        if sections:
            sections.append("")
        sections.append(f"equities ({len(wl.equities)})")
        sections.append("-" * len(sections[-1]))
        for e in wl.equities:
            cik = e.cik or "-"
            sections.append(f"  {e.symbol:<8} {e.tier:<10} {cik:<12} {e.name}")
    if sleeve in (None, "macro"):
        if sections:
            sections.append("")
        sections.append(f"macro ({len(wl.macro)})")
        sections.append("-" * len(sections[-1]))
        for m in wl.macro:
            sections.append(f"  {m.series_id:<14} {m.name}")
    return "\n".join(sections) if sections else "(no entries matched)"


@app.command("list")
def list_cmd(
    sleeve: Annotated[
        Optional[str],
        typer.Option(
            "--sleeve",
            help="Filter to one sleeve: crypto | equity | macro.",
        ),
    ] = None,
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show watchlist assets by sleeve."""
    if sleeve is not None and sleeve not in {"crypto", "equity", "equities", "macro"}:
        raise typer.BadParameter("--sleeve must be crypto, equity, or macro.")
    wl = _load_or_exit(config)
    if json_out:
        payload: dict[str, Any] = {}
        if sleeve in (None, "crypto"):
            payload["crypto"] = [
                {
                    "symbol": c.symbol,
                    "name": c.name,
                    "coingecko_id": c.coingecko_id,
                    "tier": c.tier,
                }
                for c in wl.crypto
            ]
        if sleeve in (None, "equity", "equities"):
            payload["equities"] = [
                {"symbol": e.symbol, "name": e.name, "cik": e.cik, "tier": e.tier}
                for e in wl.equities
            ]
        if sleeve in (None, "macro"):
            payload["macro"] = [
                {"series_id": m.series_id, "name": m.name} for m in wl.macro
            ]
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(_format_list_human(wl, sleeve=sleeve))


# ---------------------------------------------------------------------------
# `genkei watchlist health`
# ---------------------------------------------------------------------------

# (source, endpoint) → primary table whose row count we surface as
# the canonical "is data flowing?" signal. Used by both health (table
# row counts) and gaps (per-asset freshness queries).
PRIMARY_TABLES: dict[str, list[str]] = {
    "defillama": ["defillama.chain_tvl", "defillama.protocol_tvl"],
    "fred": ["fred.observations"],
    "sec": ["sec.companies", "sec.filings", "sec.facts"],
    "coingecko": ["coingecko.market_data"],
}

# Endpoints we expect to see in meta.ingest_runs per source. Missing
# entirely means the cron has never run successfully — louder than
# "ran 5 days ago".
EXPECTED_ENDPOINTS: dict[str, list[str]] = {
    "defillama": ["collect", "normalize"],
    "fred": ["collect", "normalize"],
    "sec": ["collect", "normalize"],
    "coingecko": ["collect", "normalize"],
}


def _table_identifier(table: str) -> sql.Identifier:
    return sql.Identifier(*table.split(".", 1))


def _query_source_health() -> list[dict[str, Any]]:
    """Per (source, endpoint) latest run with status + age, plus table liveness."""
    runs_sql = """
        SELECT source, endpoint, status, started_at, finished_at,
               substr(coalesce(error, ''), 1, 200) AS error_snippet
        FROM meta.ingest_runs r1
        WHERE started_at = (
            SELECT max(started_at) FROM meta.ingest_runs r2
            WHERE r2.source = r1.source AND r2.endpoint = r1.endpoint
        )
        ORDER BY source, endpoint
    """
    out: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(runs_sql)
        seen: set[tuple[str, str]] = set()
        for source, endpoint, status, started, finished, err in cur.fetchall():
            age_h = (now - started).total_seconds() / 3600 if started else None
            out.append(
                {
                    "source": source,
                    "endpoint": endpoint,
                    "status": status,
                    "last_started_at": started.isoformat() if started else None,
                    "last_finished_at": finished.isoformat() if finished else None,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                    "error": err if err else None,
                }
            )
            seen.add((source, endpoint))
        # Surface expected-but-never-run pairs as explicit MISSING entries
        # — silent absence is what bit us last time.
        for source, endpoints in EXPECTED_ENDPOINTS.items():
            for endpoint in endpoints:
                if (source, endpoint) not in seen:
                    out.append(
                        {
                            "source": source,
                            "endpoint": endpoint,
                            "status": "missing",
                            "last_started_at": None,
                            "last_finished_at": None,
                            "age_hours": None,
                            "error": None,
                        }
                    )
        # Per-table liveness without exact row-count scans on large hypertables.
        for source, tables in PRIMARY_TABLES.items():
            for table in tables:
                try:
                    cur.execute(
                        sql.SQL("SELECT EXISTS (SELECT 1 FROM {} LIMIT 1)").format(
                            _table_identifier(table)
                        )
                    )
                    has_rows = bool(cur.fetchone()[0])
                except Exception as exc:  # noqa: BLE001 — defensive
                    has_rows = None
                    err_msg = str(exc)
                else:
                    err_msg = None
                out.append(
                    {
                        "source": source,
                        "table": table,
                        "has_rows": has_rows,
                        "error": err_msg,
                    }
                )
    return out


def _health_status_tag(row: dict[str, Any], *, stale_hours: float) -> str:
    """Render one of OK / STALE / FAIL / MISSING / EMPTY for a row."""
    if "has_rows" in row:
        if row.get("error"):
            return "FAIL"
        if not row["has_rows"]:
            return "EMPTY"
        return "OK"
    if "row_count" in row:
        if row.get("error"):
            return "FAIL"
        if row["row_count"] == 0:
            return "EMPTY"
        return "OK"
    status = row.get("status")
    if status == "missing":
        return "MISSING"
    if status != "success":
        return "FAIL"
    age = row.get("age_hours")
    if age is not None and age > stale_hours:
        return "STALE"
    return "OK"


def _with_health_status(
    rows: list[dict[str, Any]], *, stale_hours: float
) -> list[dict[str, Any]]:
    return [
        {**row, "health_status": _health_status_tag(row, stale_hours=stale_hours)}
        for row in rows
    ]


def _format_health_human(rows: list[dict[str, Any]], *, stale_hours: float) -> str:
    runs = [r for r in rows if "endpoint" in r]
    counts = [r for r in rows if "table" in r]
    lines: list[str] = []
    lines.append(f"Source ingest runs (stale = no successful run in {stale_hours}h)")
    lines.append("-" * len(lines[-1]))
    lines.append(f"  {'source':<11} {'endpoint':<10} {'status':<8} {'age':>10}  notes")
    for r in runs:
        tag = _health_status_tag(r, stale_hours=stale_hours)
        age = (
            f"{r['age_hours']}h"
            if r["age_hours"] is not None
            else "-"
        )
        notes = r["error"][:70] if r["error"] else ""
        lines.append(
            f"  {r['source']:<11} {r['endpoint']:<10} {tag:<8} {age:>10}  {notes}"
        )
    lines.append("")
    lines.append("Primary table liveness (EMPTY = downstream queries return nothing)")
    lines.append("-" * len(lines[-1]))
    lines.append(f"  {'source':<11} {'table':<26} {'status':<8} {'data':>8}")
    for r in counts:
        tag = _health_status_tag(r, stale_hours=stale_hours)
        if "has_rows" in r:
            data = "yes" if r["has_rows"] else "no" if r["has_rows"] is not None else "-"
        else:
            data = f"{r['row_count']:,}" if r["row_count"] is not None else "-"
        lines.append(f"  {r['source']:<11} {r['table']:<26} {tag:<8} {data:>8}")
    return "\n".join(lines)


@app.command("health")
def health_cmd(
    stale_hours: Annotated[
        float,
        typer.Option(
            "--stale-hours",
            help="A successful run older than this is tagged STALE.",
            min=1,
        ),
    ] = 36.0,
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show per-source ingest health + primary-table liveness."""
    rows = _query_source_health()
    if json_out:
        typer.echo(
            json.dumps(_with_health_status(rows, stale_hours=stale_hours), indent=2)
        )
    else:
        typer.echo(_format_health_human(rows, stale_hours=stale_hours))


# ---------------------------------------------------------------------------
# `genkei watchlist gaps`
# ---------------------------------------------------------------------------


def _query_asset_gaps(wl: Watchlist) -> list[dict[str, Any]]:
    """Per asset: when was the last data point we have."""
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    with db.connection() as conn, conn.cursor() as cur:
        # Crypto → coingecko.market_data keyed by coingecko_id
        for c in wl.crypto:
            cur.execute(
                "SELECT max(ts) FROM coingecko.market_data WHERE coingecko_id = %s",
                [c.coingecko_id],
            )
            last_ts = cur.fetchone()[0]
            age_h = (now - last_ts).total_seconds() / 3600 if last_ts else None
            out.append(
                {
                    "sleeve": "crypto",
                    "asset": c.symbol,
                    "key": c.coingecko_id,
                    "source": "coingecko.market_data",
                    "last_ts": last_ts.isoformat() if last_ts else None,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                }
            )
        # Equity → sec.filings keyed by cik (skip entries without cik)
        for e in wl.equities:
            if e.cik is None:
                out.append(
                    {
                        "sleeve": "equity",
                        "asset": e.symbol,
                        "key": None,
                        "source": "sec.filings",
                        "last_ts": None,
                        "age_hours": None,
                        "note": "no CIK in watchlist",
                    }
                )
                continue
            cur.execute(
                "SELECT max(filed_at) FROM sec.filings WHERE cik = %s",
                [e.cik],
            )
            last_d = cur.fetchone()[0]
            if last_d is not None:
                last_dt = datetime.combine(last_d, datetime.min.time(), tzinfo=timezone.utc)
                age_h = (now - last_dt).total_seconds() / 3600
            else:
                last_dt = None
                age_h = None
            out.append(
                {
                    "sleeve": "equity",
                    "asset": e.symbol,
                    "key": e.cik,
                    "source": "sec.filings",
                    "last_ts": last_dt.isoformat() if last_dt else None,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                }
            )
        # Macro → fred.observations keyed by series_id
        for m in wl.macro:
            cur.execute(
                "SELECT max(ts) FROM fred.observations WHERE series_id = %s",
                [m.series_id],
            )
            last_ts = cur.fetchone()[0]
            age_h = (now - last_ts).total_seconds() / 3600 if last_ts else None
            out.append(
                {
                    "sleeve": "macro",
                    "asset": m.series_id,
                    "key": m.series_id,
                    "source": "fred.observations",
                    "last_ts": last_ts.isoformat() if last_ts else None,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                }
            )
    return out


def _format_gaps_human(rows: list[dict[str, Any]], *, threshold_hours: float) -> str:
    lines = [
        f"Per-asset freshness (GAP = no data within {threshold_hours}h, "
        "NONE = no data at all)"
    ]
    lines.append("-" * len(lines[-1]))
    lines.append(
        f"  {'sleeve':<8} {'asset':<10} {'source':<25} "
        f"{'last_ts':<25} {'age':>10}  status"
    )
    gap_count = none_count = 0
    for r in _with_gap_status(rows, threshold_hours=threshold_hours):
        if r["status"] == "NONE":
            none_count += 1
        elif r["status"] == "GAP":
            gap_count += 1
        last = r["last_ts"][:25] if r["last_ts"] else "-"
        age = f"{r['age_hours']}h" if r["age_hours"] is not None else "-"
        note = f"  {r.get('note', '')}" if r.get("note") else ""
        lines.append(
            f"  {r['sleeve']:<8} {r['asset']:<10} {r['source']:<25} "
            f"{last:<25} {age:>10}  {r['status']}{note}"
        )
    lines.append("")
    lines.append(
        f"Summary: {len(rows)} assets, {gap_count} GAP, {none_count} NONE"
    )
    return "\n".join(lines)


def _gap_status_tag(row: dict[str, Any], *, threshold_hours: float) -> str:
    if row["last_ts"] is None:
        return "NONE"
    if row["age_hours"] is not None and row["age_hours"] > threshold_hours:
        return "GAP"
    return "OK"


def _with_gap_status(
    rows: list[dict[str, Any]], *, threshold_hours: float
) -> list[dict[str, Any]]:
    return [
        {**row, "status": _gap_status_tag(row, threshold_hours=threshold_hours)}
        for row in rows
    ]


@app.command("gaps")
def gaps_cmd(
    threshold_hours: Annotated[
        float,
        typer.Option(
            "--threshold-hours",
            help=(
                "Per-asset last-data older than this is tagged GAP. "
                "Default 36h fits daily series; for monthly/quarterly FRED "
                "series pass --threshold-hours 720 (30d) or higher."
            ),
            min=1,
        ),
    ] = 36.0,
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show per-asset freshness across all sleeves."""
    wl = _load_or_exit(config)
    rows = _query_asset_gaps(wl)
    if json_out:
        typer.echo(
            json.dumps(_with_gap_status(rows, threshold_hours=threshold_hours), indent=2)
        )
    else:
        typer.echo(_format_gaps_human(rows, threshold_hours=threshold_hours))
