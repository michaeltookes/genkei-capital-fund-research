"""``genkei insiders`` — query Form 4 insider transactions (B-079).

Joins ``sec.form4_transactions`` with ``sec.insiders`` so a single
query returns the human-readable reporter name + relationship flags
alongside the transaction. Ticker resolves to ``cik`` via the
watchlist (same path the rest of the CLI uses).

Two scope modes:

* **By issuer** (``--ticker``) — recent transactions on that company,
  newest first. The default view.
* **By reporter** (``--reporter-cik``) — recent transactions by one
  insider across every issuer they file on. Useful for tracking a
  serial buyer/seller across positions.

Transaction-code filter (``--code``) lets you scope to specific action
types: ``P`` = open-market purchase, ``S`` = open-market sale,
``A`` = grant/award (compensation), ``F`` = tax-withholding sale,
``M`` = option exercise, ``G`` = gift. Default surfaces all.

Usage:
  genkei insiders --ticker AAPL                            recent activity
  genkei insiders --ticker AAPL --code P                    only open-market buys
  genkei insiders --ticker AAPL --since 2024-01-01
  genkei insiders --reporter-cik 0001214156                 across companies
  genkei insiders --ticker AAPL --json
"""

import json
from datetime import date
from decimal import Decimal
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


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _parse_date(raw: Optional[str], *, label: str) -> Optional[date]:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--{label} must be YYYY-MM-DD: {raw}") from exc


def _query_by_issuer(
    cik: str,
    *,
    code: Optional[str],
    since: Optional[date],
    until: Optional[date],
    derivative: Optional[bool],
    limit: int,
) -> list[dict[str, Any]]:
    sql = """
        SELECT t.transaction_date, t.transaction_code, t.acquired_disposed,
               t.shares, t.price_usd, t.post_transaction_shares,
               t.is_derivative, t.security_title, t.ownership_type,
               i.reporter_name, t.reporter_cik,
               t.is_director, t.is_officer, t.is_ten_percent_owner,
               t.officer_title, t.accession_number
        FROM sec.form4_transactions t
        JOIN sec.insiders i USING (reporter_cik)
        WHERE t.issuer_cik = %s
    """
    return _execute_transaction_query(
        sql,
        [cik],
        code=code,
        since=since,
        until=until,
        derivative=derivative,
        limit=limit,
    )


def _query_by_reporter(
    reporter_cik: str,
    *,
    code: Optional[str],
    since: Optional[date],
    until: Optional[date],
    derivative: Optional[bool],
    limit: int,
) -> list[dict[str, Any]]:
    sql = """
        SELECT t.transaction_date, t.transaction_code, t.acquired_disposed,
               t.shares, t.price_usd, t.post_transaction_shares,
               t.is_derivative, t.security_title, t.ownership_type,
               i.reporter_name, t.reporter_cik,
               t.is_director, t.is_officer, t.is_ten_percent_owner,
               t.officer_title, t.accession_number,
               c.ticker AS issuer_ticker, c.name AS issuer_name
        FROM sec.form4_transactions t
        JOIN sec.insiders i USING (reporter_cik)
        JOIN sec.companies c ON c.cik = t.issuer_cik
        WHERE t.reporter_cik = %s
    """
    return _execute_transaction_query(
        sql,
        [reporter_cik],
        code=code,
        since=since,
        until=until,
        derivative=derivative,
        limit=limit,
        include_issuer=True,
    )


def _execute_transaction_query(
    base_sql: str,
    base_params: list[Any],
    *,
    code: Optional[str],
    since: Optional[date],
    until: Optional[date],
    derivative: Optional[bool],
    limit: int,
    include_issuer: bool = False,
) -> list[dict[str, Any]]:
    sql = base_sql
    params: list[Any] = list(base_params)
    if code is not None:
        sql += " AND t.transaction_code = %s"
        params.append(code.upper())
    if since is not None:
        sql += " AND t.transaction_date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND t.transaction_date <= %s"
        params.append(until)
    if derivative is True:
        sql += " AND t.is_derivative = true"
    elif derivative is False:
        sql += " AND t.is_derivative = false"
    sql += " ORDER BY t.transaction_date DESC, t.accession_number DESC, t.transaction_idx LIMIT %s"
    params.append(limit)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        record: dict[str, Any] = {
            "transaction_date": row[0].isoformat() if row[0] else None,
            "transaction_code": row[1],
            "acquired_disposed": row[2],
            "shares": row[3],
            "price_usd": row[4],
            "post_transaction_shares": row[5],
            "is_derivative": row[6],
            "security_title": row[7],
            "ownership_type": row[8],
            "reporter_name": row[9],
            "reporter_cik": row[10],
            "is_director": row[11],
            "is_officer": row[12],
            "is_ten_percent_owner": row[13],
            "officer_title": row[14],
            "accession_number": row[15],
        }
        if include_issuer:
            record["issuer_ticker"] = row[16]
            record["issuer_name"] = row[17]
        out.append(record)
    return out


def _format_decimal(value: Any, *, precision: int = 0) -> str:
    if value is None:
        return "n/a"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    fmt = f",.{precision}f"
    return format(f, fmt)


def _format_human(
    *,
    title: str,
    rows: list[dict[str, Any]],
    include_issuer: bool = False,
    horizon_tag: Optional[str] = None,
) -> str:
    if not rows:
        tag = f" [horizon={horizon_tag}]" if horizon_tag is not None else ""
        return (
            f"No insider transactions for {title} (sec.form4_transactions){tag}. "
            "Try widening --since, removing --code, or check the company has "
            "Form 4 filings parsed (`genkei watchlist health` shows the table)."
        )
    horizon = f", horizon={horizon_tag}" if horizon_tag is not None else ""
    header = (
        f"{title} insider transactions "
        f"({len(rows)} row{'s' if len(rows) != 1 else ''}{horizon})"
    )
    lines = [header, "-" * len(header)]
    if include_issuer:
        # Reporter-scoped view — show issuer ticker in the first column.
        lines.append(
            f"  {'date':<12} {'tkr':<6} {'code':<5} {'shares':>14} "
            f"{'price':>10} {'reporter':<28} role"
        )
    else:
        lines.append(
            f"  {'date':<12} {'code':<5} {'shares':>14} {'price':>10} "
            f"{'reporter':<28} role"
        )
    for r in rows:
        d = r["transaction_date"] or "-"
        code = (r["transaction_code"] or "-") + (
            (r["acquired_disposed"] or "") if r["acquired_disposed"] else ""
        )
        shares = _format_decimal(r["shares"], precision=0)
        price = _format_decimal(r["price_usd"], precision=2)
        reporter = (r["reporter_name"] or "-")[:28]
        role = _format_role(r)
        if include_issuer:
            tkr = (r.get("issuer_ticker") or "-")[:6]
            lines.append(
                f"  {d:<12} {tkr:<6} {code:<5} {shares:>14} {price:>10} "
                f"{reporter:<28} {role}"
            )
        else:
            lines.append(
                f"  {d:<12} {code:<5} {shares:>14} {price:>10} "
                f"{reporter:<28} {role}"
            )
    return "\n".join(lines)


def _format_role(row: dict[str, Any]) -> str:
    flags = []
    if row.get("is_officer"):
        title = row.get("officer_title")
        flags.append(f"officer({title})" if title else "officer")
    if row.get("is_director"):
        flags.append("director")
    if row.get("is_ten_percent_owner"):
        flags.append("10%-owner")
    return ", ".join(flags) if flags else "-"


def _horizon_tag(equity: EquityEntry) -> str:
    return f"equity:{equity.sleeve}:{equity.tier}"


def _reporter_horizon_tag() -> str:
    return "equity:cross-issuer:reporter"


def _tag_rows(rows: list[dict[str, Any]], horizon_tag: str) -> list[dict[str, Any]]:
    return [{**row, "horizon_tag": horizon_tag} for row in rows]


def _resolve_equity(ticker: str, watchlist: Watchlist) -> EquityEntry:
    equity = watchlist.find_equity(ticker)
    if equity is None:
        crypto = watchlist.find_crypto(ticker)
        if crypto is not None:
            raise typer.BadParameter(
                f"{ticker} is a crypto asset; Form 4 is an SEC equity filing. "
                "Use `genkei prices` for crypto market data."
            )
        raise typer.BadParameter(
            f"Ticker {ticker!r} not found in the equities watchlist."
        )
    if equity.cik is None:
        raise typer.BadParameter(
            f"{ticker} has no CIK in the watchlist — add one before querying insider activity."
        )
    return equity


def insiders_cmd(
    ticker: Annotated[
        Optional[str],
        typer.Option("--ticker", "-t", help="Equity ticker (issuer view), e.g. AAPL."),
    ] = None,
    reporter_cik: Annotated[
        Optional[str],
        typer.Option(
            "--reporter-cik",
            help="10-digit SEC CIK of the reporting insider (reporter view).",
        ),
    ] = None,
    code: Annotated[
        Optional[str],
        typer.Option(
            "--code",
            help="SEC transaction code filter (P, S, A, F, M, G, etc).",
        ),
    ] = None,
    derivative_only: Annotated[
        bool,
        typer.Option(
            "--derivative",
            help="Only derivative transactions (options, warrants).",
        ),
    ] = False,
    non_derivative_only: Annotated[
        bool,
        typer.Option(
            "--non-derivative",
            help="Only non-derivative (open-market) transactions.",
        ),
    ] = False,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Start transaction_date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="End transaction_date (YYYY-MM-DD)."),
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
    """Show SEC Form 4 insider transactions for an issuer or a reporter."""
    if ticker is None and reporter_cik is None:
        raise typer.BadParameter("Pass either --ticker or --reporter-cik.")
    if ticker is not None and reporter_cik is not None:
        raise typer.BadParameter("--ticker and --reporter-cik are mutually exclusive.")
    if derivative_only and non_derivative_only:
        raise typer.BadParameter("--derivative and --non-derivative are mutually exclusive.")
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    derivative_filter: Optional[bool] = None
    if derivative_only:
        derivative_filter = True
    elif non_derivative_only:
        derivative_filter = False

    if ticker is not None:
        try:
            watchlist = load_watchlist(config)
        except (FileNotFoundError, ValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
        equity = _resolve_equity(ticker, watchlist)
        assert equity.cik is not None
        horizon_tag = _horizon_tag(equity)
        rows = _query_by_issuer(
            equity.cik,
            code=code,
            since=since_d,
            until=until_d,
            derivative=derivative_filter,
            limit=limit,
        )
        rows = _tag_rows(rows, horizon_tag)
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(
                _format_human(
                    title=ticker.upper(), rows=rows, horizon_tag=horizon_tag
                )
            )
    else:
        assert reporter_cik is not None
        horizon_tag = _reporter_horizon_tag()
        rows = _query_by_reporter(
            reporter_cik.zfill(10),
            code=code,
            since=since_d,
            until=until_d,
            derivative=derivative_filter,
            limit=limit,
        )
        rows = _tag_rows(rows, horizon_tag)
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(
                _format_human(
                    title=f"reporter {reporter_cik.zfill(10)}",
                    rows=rows,
                    include_issuer=True,
                    horizon_tag=horizon_tag,
                )
            )
