"""``genkei holdings`` — query SEC 13F institutional holdings (B-080).

The 13F-side counterpart to ``genkei insiders``. Insiders give you
*flow* (X bought / sold on date D); 13F gives you *positioning*
(manager X held N shares as of quarter Q).

Three scopes:

* **By filer** (``--filer`` or ``--filer-cik``) — what does this
  manager hold? The most-used view. Names resolve via the watchlist
  (``Berkshire Hathaway Inc``) or pass a bare CIK.
* **By CUSIP** (``--cusip``) — who holds this security? Joins every
  filer's positions on the CUSIP. The crowding-monitor primitive.
* **Default** (no scope flag) — quick summary across all watchlist
  filers: latest period_of_report each has filed + total reported
  value. Sanity-check view.

By default returns the most recent ``period_of_report`` available;
override with ``--period YYYY-MM-DD`` (quarter end), ``--since`` /
``--until`` ranges, or ``--all-periods`` for the full history.

Output sorted by ``value_usd`` desc with a ``--top N`` cap (default 25).
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    FilerEntry,
    Watchlist,
    load_watchlist,
)


def _resolve_filer(
    identifier: str, watchlist: Watchlist
) -> tuple[str, Optional[FilerEntry]]:
    """Map ``--filer`` text or a bare CIK to ``(filer_cik, FilerEntry?)``.

    Numeric input is treated as a CIK (zero-padded). Non-numeric is
    looked up by exact case-insensitive name in the watchlist.
    Returns ``(cik, entry)`` where ``entry`` is None if the CIK was
    given directly but isn't in the watchlist (still valid — the user
    might be querying historical data for a filer that's since been
    removed).
    """
    stripped = identifier.strip()
    if stripped.isdigit():
        padded = stripped.zfill(10)
        return padded, watchlist.find_filer(padded)
    entry = watchlist.find_filer(stripped)
    if entry is None:
        raise typer.BadParameter(
            f"Filer {identifier!r} not found in the watchlist. "
            "Pass a CIK directly or add the filer to `filers:` in watchlists.yml."
        )
    return entry.filer_cik, entry


def _latest_period_for_filer(filer_cik: str) -> Optional[date]:
    """Return the most recent period_of_report for this filer's holdings."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT max(period_of_report) FROM sec.form13f_holdings "
            "WHERE filer_cik = %s",
            [filer_cik],
        )
        row = cur.fetchone()
    return row[0] if row else None


def _latest_period_for_cusip(cusip: str) -> Optional[date]:
    """Return the most recent period_of_report any filer reported for this CUSIP."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT max(period_of_report) FROM sec.form13f_holdings "
            "WHERE cusip = %s",
            [cusip],
        )
        row = cur.fetchone()
    return row[0] if row else None


def _query_by_filer(
    filer_cik: str,
    *,
    period: Optional[date],
    since: Optional[date],
    until: Optional[date],
    cusip: Optional[str],
    top: int,
) -> list[dict[str, Any]]:
    sql = """
        SELECT period_of_report, cusip, issuer_name, class_title,
               value_usd, shares_or_principal, shares_or_principal_type,
               put_call, investment_discretion, accession_number
        FROM sec.form13f_holdings
        WHERE filer_cik = %s
    """
    params: list[Any] = [filer_cik]
    if period is not None:
        sql += " AND period_of_report = %s"
        params.append(period)
    if since is not None:
        sql += " AND period_of_report >= %s"
        params.append(since)
    if until is not None:
        sql += " AND period_of_report <= %s"
        params.append(until)
    if cusip is not None:
        sql += " AND cusip = %s"
        params.append(cusip)
    sql += " ORDER BY value_usd DESC NULLS LAST, cusip ASC LIMIT %s"
    params.append(top)
    return _fetch_holding_rows(sql, params, scope="filer")


def _query_by_cusip(
    cusip: str,
    *,
    period: Optional[date],
    since: Optional[date],
    until: Optional[date],
    top: int,
) -> list[dict[str, Any]]:
    # Join sec.filers to surface manager names on the crowding view.
    sql = """
        SELECT h.period_of_report, h.cusip, h.issuer_name, h.class_title,
               h.value_usd, h.shares_or_principal, h.shares_or_principal_type,
               h.put_call, h.investment_discretion, h.accession_number,
               h.filer_cik, f.name AS filer_name
        FROM sec.form13f_holdings h
        JOIN sec.filers f ON f.filer_cik = h.filer_cik
        WHERE h.cusip = %s
    """
    params: list[Any] = [cusip]
    if period is not None:
        sql += " AND h.period_of_report = %s"
        params.append(period)
    if since is not None:
        sql += " AND h.period_of_report >= %s"
        params.append(since)
    if until is not None:
        sql += " AND h.period_of_report <= %s"
        params.append(until)
    sql += " ORDER BY h.value_usd DESC NULLS LAST, f.name ASC LIMIT %s"
    params.append(top)
    return _fetch_holding_rows(sql, params, scope="cusip")


def _fetch_holding_rows(
    sql: str, params: list[Any], *, scope: str
) -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        record: dict[str, Any] = {
            "period_of_report": row[0].isoformat() if row[0] else None,
            "cusip": row[1],
            "issuer_name": row[2],
            "class_title": row[3],
            "value_usd": row[4],
            "shares_or_principal": row[5],
            "shares_or_principal_type": row[6],
            "put_call": row[7],
            "investment_discretion": row[8],
            "accession_number": row[9],
        }
        if scope == "cusip":
            record["filer_cik"] = row[10]
            record["filer_name"] = row[11]
        out.append(record)
    return out


def _query_default_summary(filers: list[FilerEntry], top: int) -> list[dict[str, Any]]:
    """Default view: one row per watchlist filer with latest period + total value."""
    if not filers:
        return []
    ciks = [f.filer_cik for f in filers]
    sql = """
        WITH latest AS (
            SELECT filer_cik, max(period_of_report) AS latest_period
            FROM sec.form13f_holdings
            WHERE filer_cik = ANY(%s)
            GROUP BY filer_cik
        )
        SELECT h.filer_cik, latest.latest_period, count(*), sum(h.value_usd)
        FROM sec.form13f_holdings h
        JOIN latest
          ON latest.filer_cik = h.filer_cik
         AND latest.latest_period = h.period_of_report
        GROUP BY h.filer_cik, latest.latest_period
        ORDER BY sum(h.value_usd) DESC NULLS LAST
        LIMIT %s
    """
    name_by_cik = {f.filer_cik: f.name for f in filers}
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, [ciks, top])
        rows = cur.fetchall()
    return [
        {
            "filer_cik": cik,
            "filer_name": name_by_cik.get(cik) or "-",
            "latest_period": period.isoformat() if period else None,
            "holdings_count": int(count),
            "total_value_usd": total,
        }
        for (cik, period, count, total) in rows
    ]


def _format_decimal(value: Any, *, precision: int = 0) -> str:
    if value is None:
        return "n/a"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return format(f, f",.{precision}f")


def _format_filer_human(
    *,
    filer_label: str,
    filer_cik: str,
    period_label: str,
    rows: list[dict[str, Any]],
) -> str:
    if not rows:
        return (
            f"No 13F holdings found for filer {filer_label} ({filer_cik}) {period_label}. "
            "Run `genkei watchlist health` to confirm 13F ingest is healthy."
        )
    header = f"{filer_label} 13F holdings ({len(rows)} row(s), {period_label})"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'period':<12} {'cusip':<12} {'value':>16} {'shares':>14} "
        f"{'issuer':<28}"
    )
    for r in rows:
        period = r["period_of_report"] or "-"
        value = _format_decimal(r["value_usd"], precision=0)
        shares = _format_decimal(r["shares_or_principal"], precision=0)
        issuer = (r["issuer_name"] or "-")[:28]
        lines.append(
            f"  {period:<12} {r['cusip']:<12} ${value:>15} {shares:>14} {issuer:<28}"
        )
    return "\n".join(lines)


def _format_cusip_human(
    *, cusip: str, period_label: str, rows: list[dict[str, Any]]
) -> str:
    if not rows:
        return (
            f"No 13F holdings found for CUSIP {cusip} {period_label}. "
            "Add the manager who holds it to `filers:` in watchlists.yml or "
            "widen --since."
        )
    header = f"CUSIP {cusip} holders ({len(rows)} row(s), {period_label})"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'period':<12} {'value':>16} {'shares':>14} "
        f"{'filer':<32} {'discretion':<10}"
    )
    for r in rows:
        period = r["period_of_report"] or "-"
        value = _format_decimal(r["value_usd"], precision=0)
        shares = _format_decimal(r["shares_or_principal"], precision=0)
        filer = (r.get("filer_name") or "-")[:32]
        discretion = (r.get("investment_discretion") or "-")[:10]
        lines.append(
            f"  {period:<12} ${value:>15} {shares:>14} {filer:<32} {discretion:<10}"
        )
    return "\n".join(lines)


def _format_summary_human(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "No 13F holdings rows yet. Run the SEC daily workflow with the "
            "13F steps wired up; live ingest lands here within a quarter cycle."
        )
    header = "Watchlist filer 13F summary"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'filer':<36} {'latest period':<14} {'holdings':>10} {'total value':>20}"
    )
    for r in rows:
        filer = (r["filer_name"] or "-")[:36]
        period = r["latest_period"] or "-"
        count = str(r["holdings_count"])
        total = "$" + _format_decimal(r["total_value_usd"], precision=0)
        lines.append(f"  {filer:<36} {period:<14} {count:>10} {total:>20}")
    return "\n".join(lines)


def holdings_cmd(
    filer: Annotated[
        Optional[str],
        typer.Option(
            "--filer",
            help="Filer by watchlist name (e.g. 'Berkshire Hathaway Inc').",
        ),
    ] = None,
    filer_cik: Annotated[
        Optional[str],
        typer.Option(
            "--filer-cik",
            help="Filer by SEC CIK (10-digit; auto-padded).",
        ),
    ] = None,
    cusip: Annotated[
        Optional[str],
        typer.Option(
            "--cusip",
            help="Holdings of a specific 9-character CUSIP across all watchlist filers.",
        ),
    ] = None,
    period: Annotated[
        Optional[str],
        typer.Option(
            "--period",
            help="Single quarter-end (YYYY-MM-DD). Defaults to latest available.",
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest period_of_report (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest period_of_report (YYYY-MM-DD)."),
    ] = None,
    all_periods: Annotated[
        bool,
        typer.Option(
            "--all-periods",
            help="Don't restrict to the latest period; return rows from every period.",
        ),
    ] = False,
    top: Annotated[
        int,
        typer.Option("--top", help="Max rows (sorted by value_usd desc).", min=1),
    ] = 25,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of human table."),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show SEC 13F institutional holdings."""
    scope_flags = sum(1 for x in (filer, filer_cik, cusip) if x)
    if scope_flags > 1:
        raise typer.BadParameter(
            "--filer / --filer-cik / --cusip are mutually exclusive."
        )

    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    period_d = _parse_date(period, label="period")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    if period_d is not None and all_periods:
        raise typer.BadParameter(
            "--period and --all-periods are mutually exclusive."
        )

    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if filer or filer_cik:
        identifier = filer or filer_cik
        assert identifier is not None
        cik, entry = _resolve_filer(identifier, watchlist)
        # Pin to the latest period unless the user explicitly overrode.
        effective_period: Optional[date] = period_d
        period_label: str
        if effective_period is None and not all_periods and since_d is None and until_d is None:
            effective_period = _latest_period_for_filer(cik)
            period_label = (
                f"latest period {effective_period.isoformat()}"
                if effective_period is not None
                else "no holdings yet"
            )
        elif effective_period is not None:
            period_label = f"period {effective_period.isoformat()}"
        elif all_periods:
            period_label = "all periods"
        else:
            range_label = f"{since_d or 'earliest'} to {until_d or 'latest'}"
            period_label = f"range {range_label}"

        rows = _query_by_filer(
            cik,
            period=effective_period,
            since=since_d,
            until=until_d,
            cusip=None,
            top=top,
        )
        label = entry.name if entry is not None else f"CIK {cik}"
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(
                _format_filer_human(
                    filer_label=label,
                    filer_cik=cik,
                    period_label=period_label,
                    rows=rows,
                )
            )
        return

    if cusip:
        effective_period = period_d
        period_label = ""
        if effective_period is None and not all_periods and since_d is None and until_d is None:
            effective_period = _latest_period_for_cusip(cusip)
            period_label = (
                f"latest period {effective_period.isoformat()}"
                if effective_period is not None
                else "no holdings yet"
            )
        elif effective_period is not None:
            period_label = f"period {effective_period.isoformat()}"
        elif all_periods:
            period_label = "all periods"
        else:
            range_label = f"{since_d or 'earliest'} to {until_d or 'latest'}"
            period_label = f"range {range_label}"
        rows = _query_by_cusip(
            cusip,
            period=effective_period,
            since=since_d,
            until=until_d,
            top=top,
        )
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(
                _format_cusip_human(
                    cusip=cusip, period_label=period_label, rows=rows
                )
            )
        return

    # Default summary view.
    rows = _query_default_summary(watchlist.filers, top=top)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    else:
        typer.echo(_format_summary_human(rows))


# Used by tests + a guard against accidentally re-introducing the Decimal
# JSON-encoding bug (B-079 era).
def _decimal_to_str(value: Decimal) -> str:
    return str(value)
