"""``genkei cot`` — query CFTC Commitments of Traders positions (B-031).

Returns weekly position rows from ``cftc.cot_reports``. The schema is
keyed on ``(report_date, market_code, trader_category)`` — one upstream
weekly report fans out into 5 trader-category rows. The CLI lets the
caller slice by market, trader category, and date window, with
``net_position`` (long − short) computed in the output so the headline
"are leveraged funds net-long or net-short" question is one command
rather than a follow-up join.

Usage:
  genkei cot --market BTC                                    most recent N weeks
  genkei cot --market BTC --since 2024-01-01
  genkei cot --market BTC --trader-category leveraged_funds  filter to one category
  genkei cot --market BTC --json
  genkei cot --list-markets                                  list all watchlist markets

Trader-category aliases accepted (any of these resolves to the canonical column):
  - leveraged_funds, lev_money, hedge_funds      → leveraged_funds
  - asset_manager, asset_mgr, institutional      → asset_manager
  - dealer_intermediary, dealer, banks           → dealer_intermediary
  - managed_money, mm                            → managed_money
  - swap_dealer, swap                            → swap_dealer
  - producer_merchant, producer, commercial      → producer_merchant
  - other_reportables, other                     → other_reportables
  - non_reportable, retail                       → non_reportable
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default, parse_date
from genkei.common import db
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    CotMarketEntry,
    Watchlist,
    load_watchlist,
)

_parse_date = parse_date

# Caller-friendly aliases → canonical trader_category. The canonical names
# match the values stored in cftc.cot_reports.trader_category exactly.
_CATEGORY_ALIASES: dict[str, str] = {
    "leveraged_funds": "leveraged_funds",
    "lev_money": "leveraged_funds",
    "hedge_funds": "leveraged_funds",
    "asset_manager": "asset_manager",
    "asset_mgr": "asset_manager",
    "institutional": "asset_manager",
    "dealer_intermediary": "dealer_intermediary",
    "dealer": "dealer_intermediary",
    "banks": "dealer_intermediary",
    "managed_money": "managed_money",
    "mm": "managed_money",
    "swap_dealer": "swap_dealer",
    "swap": "swap_dealer",
    "producer_merchant": "producer_merchant",
    "producer": "producer_merchant",
    "commercial": "producer_merchant",
    "other_reportables": "other_reportables",
    "other": "other_reportables",
    "non_reportable": "non_reportable",
    "retail": "non_reportable",
}


def _resolve_category(raw: str) -> str:
    key = raw.strip().lower()
    if key in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[key]
    raise typer.BadParameter(
        f"Unknown trader category {raw!r}. Valid options: "
        + ", ".join(sorted(set(_CATEGORY_ALIASES.values())))
    )


def _resolve_market(symbol_or_code: str, watchlist: Watchlist) -> CotMarketEntry:
    entry = watchlist.find_cot_market(symbol_or_code)
    if entry is None:
        valid = ", ".join(m.symbol for m in watchlist.cot_markets)
        raise typer.BadParameter(
            f"Market {symbol_or_code!r} not in the COT watchlist. "
            f"Add it under `cot_markets:` in watchlists.yml, or use one of: {valid}."
        )
    return entry


def _query_cot(
    market: CotMarketEntry,
    *,
    trader_category: Optional[str],
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Query cftc.cot_reports filtered by market + optional category + date window."""
    where = "market_code = %s"
    params: list[Any] = [market.code]
    if trader_category is not None:
        where += " AND trader_category = %s"
        params.append(trader_category)
    if since is not None:
        where += " AND report_date >= %s"
        params.append(since)
    if until is not None:
        where += " AND report_date <= %s"
        params.append(until)
    sql = (
        "SELECT report_date, trader_category, long_positions, short_positions, "
        "spreading_positions, market_name, report_type "
        f"FROM cftc.cot_reports WHERE {where} "
        "ORDER BY report_date DESC, trader_category LIMIT %s"
    )
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for report_date, category, long_pos, short_pos, spread_pos, market_name, report_type in rows:
        long_val = int(long_pos) if long_pos is not None else None
        short_val = int(short_pos) if short_pos is not None else None
        net: Optional[int] = (
            None if long_val is None or short_val is None else long_val - short_val
        )
        out.append(
            {
                "report_date": report_date.isoformat()
                if isinstance(report_date, (date, datetime))
                else None,
                "market_code": market.code,
                "market_name": market_name,
                "report_type": report_type,
                "trader_category": category,
                "long_positions": long_val,
                "short_positions": short_val,
                "spreading_positions": int(spread_pos) if spread_pos is not None else None,
                "net_position": net,
            }
        )
    return out


def _fmt(value: Optional[int], width: int, *, sign: bool = False) -> str:
    if value is None:
        return f"{'-':>{width}}"
    return f"{value:>+{width},}" if sign else f"{value:>{width},}"


def _format_human(
    market: CotMarketEntry,
    rows: list[dict[str, Any]],
    *,
    trader_category: Optional[str],
    horizon_tag: str,
) -> str:
    if not rows:
        hint = "Widen --since, drop --trader-category, or check `genkei cot --markets`."
        return f"No COT rows for {market.symbol} ({market.code}). {hint}"
    header_bits = [f"{market.symbol} ({market.code})", market.report_type]
    if trader_category is not None:
        header_bits.append(f"category={trader_category}")
    header_bits.append(f"horizon={horizon_tag}")
    header_bits.append(f"{len(rows)} row{'s' if len(rows) != 1 else ''}")
    header = " | ".join(header_bits)
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'report_date':<12} {'category':<22} {'long':>10} {'short':>10} "
        f"{'spread':>10} {'net':>12}"
    )
    for row in rows:
        rd = row["report_date"] or "-"
        cat = row["trader_category"]
        long_val = _fmt(row["long_positions"], 10)
        short_val = _fmt(row["short_positions"], 10)
        spread_val = _fmt(row["spreading_positions"], 10)
        net_val = _fmt(row["net_position"], 12, sign=True)
        lines.append(f"  {rd:<12} {cat:<22} {long_val} {short_val} {spread_val} {net_val}")
    return "\n".join(lines)


def _format_markets_human(watchlist: Watchlist) -> str:
    if not watchlist.cot_markets:
        return "No cot_markets configured. Edit watchlists.yml."
    lines = ["Configured COT markets:", "-" * 24]
    for m in watchlist.cot_markets:
        rat = f"  {m.rationale}" if m.rationale else ""
        lines.append(
            f"  {m.symbol:<5} code={m.code:<8} type={m.report_type:<14} "
            f"sleeve={m.sleeve:<14} {m.name}{rat}"
        )
    return "\n".join(lines)


def _horizon_tag(market: CotMarketEntry) -> str:
    return f"cot:{market.sleeve}"


def _tag_rows(rows: list[dict[str, Any]], horizon_tag: str) -> list[dict[str, Any]]:
    return [{**row, "horizon_tag": horizon_tag} for row in rows]


def cot_cmd(
    market: Annotated[
        Optional[str],
        typer.Option(
            "--market",
            "-m",
            help="Market symbol (BTC, ETH, ES, GC, CL) or CFTC market code (133741).",
        ),
    ] = None,
    trader_category: Annotated[
        Optional[str],
        typer.Option(
            "--trader-category",
            "-c",
            help="Filter to one trader category (e.g. leveraged_funds, asset_manager). "
            "Aliases accepted; see module docstring.",
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest report_date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest report_date (YYYY-MM-DD)."),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="Max rows.", min=1)
    ] = 50,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of human table."),
    ] = False,
    list_markets: Annotated[
        bool,
        typer.Option(
            "--list-markets",
            help="List configured COT markets and exit.",
        ),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Query CFTC Commitments of Traders weekly position breakdowns."""
    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if list_markets:
        if json_out:
            typer.echo(
                json.dumps(
                    [
                        {
                            "code": m.code,
                            "symbol": m.symbol,
                            "name": m.name,
                            "report_type": m.report_type,
                            "sleeve": m.sleeve,
                            "rationale": m.rationale,
                        }
                        for m in watchlist.cot_markets
                    ],
                    indent=2,
                    default=json_default,
                )
            )
        else:
            typer.echo(_format_markets_human(watchlist))
        return

    if market is None:
        raise typer.BadParameter(
            "--market is required (or use --list-markets to list watchlist entries)."
        )

    market_entry = _resolve_market(market, watchlist)
    canonical_category = _resolve_category(trader_category) if trader_category else None
    since_d = parse_date(since, label="since")
    until_d = parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    rows = _query_cot(
        market_entry,
        trader_category=canonical_category,
        since=since_d,
        until=until_d,
        limit=limit,
    )
    horizon_tag = _horizon_tag(market_entry)
    rows = _tag_rows(rows, horizon_tag)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=json_default))
    else:
        typer.echo(
            _format_human(
                market_entry,
                rows,
                trader_category=canonical_category,
                horizon_tag=horizon_tag,
            )
        )
