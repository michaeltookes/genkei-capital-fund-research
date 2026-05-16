"""``genkei filings`` — query SEC EDGAR filings + XBRL facts (B-040).

Two modes, switched by whether ``--concept`` is passed:

* **Filings index** (default) — recent filings for an equity ticker,
  ordered by filed_at DESC. Filterable by --form / --since / --until.
* **XBRL facts** (--concept) — per-period numeric facts from
  ``sec.facts`` for the given concept, filterable by --unit / --since
  / --until. Concept matching is case-insensitive and matches either
  the bare concept (``Revenues``) or the namespaced form
  (``us-gaap:Revenues``).

Usage:
  genkei filings --ticker AAPL                            recent filings
  genkei filings --ticker AAPL --form 10-K                 only 10-Ks
  genkei filings --ticker AAPL --since 2024-01-01          since date
  genkei filings --ticker AAPL --concept Revenues          XBRL revenues
  genkei filings --ticker AAPL --concept Revenues --unit USD --since 2020-01-01
  genkei filings --ticker AAPL --json                      machine output
"""

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EquityEntry,
    Watchlist,
    load_watchlist,
)
from genkei.common import db


def _parse_date(raw: Optional[str], *, label: str) -> Optional[date]:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--{label} must be YYYY-MM-DD: {raw}") from exc


def _query_filings(
    cik: str,
    *,
    form: Optional[str],
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT accession_number, form_type, filed_at, report_date, "
        "primary_document, primary_doc_description, items, is_xbrl "
        "FROM sec.filings WHERE cik = %s"
    )
    params: list[Any] = [cik]
    if form is not None:
        sql += " AND form_type = %s"
        params.append(form)
    if since is not None:
        sql += " AND filed_at >= %s"
        params.append(since)
    if until is not None:
        sql += " AND filed_at <= %s"
        params.append(until)
    sql += " ORDER BY filed_at DESC, accepted_at DESC LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "accession_number": accn,
            "form_type": form_t,
            "filed_at": filed.isoformat() if filed is not None else None,
            "report_date": rd.isoformat() if rd is not None else None,
            "primary_document": doc,
            "primary_doc_description": desc,
            "items": items,
            "is_xbrl": bool(is_xbrl) if is_xbrl is not None else None,
        }
        for (accn, form_t, filed, rd, doc, desc, items, is_xbrl) in rows
    ]


def _query_facts(
    cik: str,
    *,
    concept: str,
    unit: Optional[str],
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    # The concept column stores the namespaced form (``us-gaap:Revenues``);
    # ``taxonomy`` repeats the prefix as a separate column. Accept either
    # form from the user — bare matches by suffix (any taxonomy),
    # namespaced matches exact (case-insensitive).
    sql = (
        "SELECT taxonomy, concept, unit, period_start, period_end, value, "
        "accession_number, form_type, filed_at, fy, fp "
        "FROM sec.facts WHERE cik = %s"
    )
    params: list[Any] = [cik]
    if ":" in concept:
        sql += " AND lower(concept) = lower(%s)"
        params.append(concept)
    else:
        # Match "<taxonomy>:<concept>" by the part after the colon.
        sql += " AND lower(split_part(concept, ':', 2)) = lower(%s)"
        params.append(concept)
    if unit is not None:
        sql += " AND unit = %s"
        params.append(unit)
    if since is not None:
        sql += " AND period_end >= %s"
        params.append(since)
    if until is not None:
        sql += " AND period_end <= %s"
        params.append(until)
    sql += " ORDER BY period_end DESC, filed_at DESC LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "taxonomy": tx,
            "concept": cn,
            "unit": un,
            "period_start": ps.isoformat() if ps is not None else None,
            "period_end": pe.isoformat() if pe is not None else None,
            "value": float(val) if val is not None else None,
            "accession_number": accn,
            "form_type": ft,
            "filed_at": fa.isoformat() if fa is not None else None,
            "fy": fy,
            "fp": fp,
        }
        for (tx, cn, un, ps, pe, val, accn, ft, fa, fy, fp) in rows
    ]


def _format_filings_human(ticker: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            f"No filings for {ticker} (sec.filings). "
            "Try widening --since, removing --form, or check the company is "
            "covered by the SEC ingester."
        )
    header = f"{ticker} filings ({len(rows)} row{'s' if len(rows) != 1 else ''})"
    lines = [header, "-" * len(header)]
    lines.append(f"  {'filed':<12} {'form':<10} {'report':<12}  accession")
    for r in rows:
        filed = r["filed_at"] or "-"
        form = r["form_type"] or "-"
        rd = r["report_date"] or "-"
        accn = r["accession_number"]
        lines.append(f"  {filed:<12} {form:<10} {rd:<12}  {accn}")
    return "\n".join(lines)


def _format_facts_human(ticker: str, concept: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            f"No facts for {ticker} concept={concept!r} (sec.facts). "
            "Try a different --unit (e.g. USD, shares, USD/shares), widen "
            "--since, or check `--concept` spelling (us-gaap:Revenues)."
        )
    header = f"{ticker} {concept} ({len(rows)} row{'s' if len(rows) != 1 else ''})"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'period_end':<12} {'fy':<5} {'fp':<4} {'unit':<10} "
        f"{'value':>20}  {'form':<6} accession"
    )
    for r in rows:
        pe = r["period_end"] or "-"
        fy = str(r["fy"]) if r["fy"] is not None else "-"
        fp = r["fp"] or "-"
        unit = r["unit"] or "-"
        val = f"{r['value']:>20,.0f}" if r["value"] is not None else f"{'n/a':>20}"
        form = r["form_type"] or "-"
        accn = r["accession_number"]
        lines.append(f"  {pe:<12} {fy:<5} {fp:<4} {unit:<10} {val}  {form:<6} {accn}")
    return "\n".join(lines)


def _resolve_equity(ticker: str, watchlist: Watchlist) -> EquityEntry:
    equity = watchlist.find_equity(ticker)
    if equity is None:
        crypto = watchlist.find_crypto(ticker)
        if crypto is not None:
            raise typer.BadParameter(
                f"{ticker} is a crypto asset; SEC EDGAR only covers equities. "
                "Use `genkei prices` for crypto market data."
            )
        raise typer.BadParameter(
            f"Ticker {ticker!r} not found in the equities watchlist."
        )
    if equity.cik is None:
        raise typer.BadParameter(
            f"{ticker} has no CIK in the watchlist — add one before "
            "querying SEC filings."
        )
    return equity


def filings_cmd(
    ticker: Annotated[str, typer.Option("--ticker", "-t", help="Equity ticker, e.g. AAPL.")],
    form: Annotated[
        Optional[str],
        typer.Option("--form", help="Filter by SEC form type, e.g. 10-K, 8-K, 4."),
    ] = None,
    concept: Annotated[
        Optional[str],
        typer.Option(
            "--concept",
            help="XBRL concept, e.g. Revenues or us-gaap:Revenues. "
            "Switches output to sec.facts.",
        ),
    ] = None,
    unit: Annotated[
        Optional[str],
        typer.Option("--unit", help="Filter facts by unit, e.g. USD, shares, USD/shares."),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option(
            "--since",
            help="Start date (YYYY-MM-DD). filed_at for filings, period_end for facts.",
        ),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="End date (YYYY-MM-DD)."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.", min=1)] = 30,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of human table."),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show SEC filings (default) or XBRL facts (--concept) for an equity."""
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    if unit is not None and concept is None:
        raise typer.BadParameter("--unit only applies with --concept (filters sec.facts).")

    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    equity = _resolve_equity(ticker, watchlist)
    assert equity.cik is not None  # _resolve_equity guarantees

    if concept is not None:
        rows = _query_facts(
            equity.cik,
            concept=concept,
            unit=unit,
            since=since_d,
            until=until_d,
            limit=limit,
        )
        if json_out:
            typer.echo(json.dumps(rows, indent=2))
        else:
            typer.echo(_format_facts_human(ticker.upper(), concept, rows))
    else:
        rows = _query_filings(
            equity.cik,
            form=form,
            since=since_d,
            until=until_d,
            limit=limit,
        )
        if json_out:
            typer.echo(json.dumps(rows, indent=2))
        else:
            typer.echo(_format_filings_human(ticker.upper(), rows))
