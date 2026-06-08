"""``genkei whales`` — ETH whale-flow aggregate query (B-106).

Reads ``onchain.eth_whale_flows`` (per-address rows) and
``onchain.eth_whale_flows_aggregate`` (per-category-per-day view), with
a category / since / address filter set.

**Critical reading note**: exchange-category inflow REVERSES the
intuitive sign. A positive ``net_flow_eth`` on the exchange category
means users sent ETH TO exchanges (sell pressure), NOT that the
exchange itself bought ETH. The other three categories follow the
intuitive read. See ``docs/sources/eth-whale-addresses.md`` for the
full set of category sign conventions + the four hard limits the data
exposes (incomplete address list, sign inversion, rate-limit ceiling,
24h-boundary noise).

Usage:
  genkei whales                                         all categories, latest N days
  genkei whales --category exchange                      one category
  genkei whales --category exchange --since 2026-05-01
  genkei whales --address 0xde0b...                     per-address rows (not aggregate)
  genkei whales --json
  genkei whales --list-addresses                        list configured addresses

Category aliases accepted: ``exchange`` / ``cex``; ``custodian`` / ``staking``;
``foundation`` / ``treasury``; ``whale``.
"""

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import (
    json_default as _json_default,
)
from genkei.cli._helpers import (
    parse_date as _parse_date,
)
from genkei.common import db
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EthWhaleAddressEntry,
    Watchlist,
    load_watchlist,
)

_CATEGORY_ALIASES: dict[str, str] = {
    "exchange": "exchange",
    "cex": "exchange",
    "custodian": "custodian",
    "staking": "custodian",
    "foundation": "foundation",
    "treasury": "foundation",
    "whale": "whale",
}
_VALID_CATEGORIES = {"exchange", "custodian", "foundation", "whale"}
_HORIZON_FOOTER = (
    "  Horizon: mid-to-long-term | "
    "sleeve: crypto-core (ETH) + crypto-tactical"
)


def _resolve_category(raw: str) -> str:
    """Map a CLI-friendly category alias to the canonical column value."""
    key = raw.strip().lower()
    if key in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[key]
    raise typer.BadParameter(
        f"Unknown category {raw!r}. Valid: {sorted(_VALID_CATEGORIES)} "
        f"(aliases: cex=exchange, staking=custodian, treasury=foundation)."
    )


def _format_address_list_human(watchlist: Watchlist) -> str:
    """Render the configured whale addresses grouped by category."""
    if not watchlist.eth_whale_addresses:
        return "No eth_whale_addresses configured. Edit watchlists.yml."
    lines = ["Configured ETH whale addresses:", "-" * 32]
    by_cat: dict[str, list[EthWhaleAddressEntry]] = {}
    for entry in watchlist.eth_whale_addresses:
        by_cat.setdefault(entry.category, []).append(entry)
    for cat in sorted(by_cat):
        lines.append(f"\n{cat}:")
        for e in by_cat[cat]:
            lines.append(f"  {e.address}  {e.label}")
    return "\n".join(lines)


def _query_aggregate(
    *,
    category: Optional[str],
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Pull rows from the per-category-per-day aggregate view."""
    sql = (
        "SELECT category, day, address_count, "
        "total_balance_eth, total_balance_usd, "
        "net_flow_eth, net_flow_usd, tx_count "
        "FROM onchain.eth_whale_flows_aggregate WHERE 1=1"
    )
    params: list[Any] = []
    if category is not None:
        sql += " AND category = %s"
        params.append(category)
    if since is not None:
        sql += " AND day >= %s"
        params.append(since)
    if until is not None:
        sql += " AND day <= %s"
        params.append(until)
    sql += " ORDER BY day DESC, category LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "category": cat,
            "day": d.isoformat() if isinstance(d, date) else None,
            "address_count": int(ac) if ac is not None else None,
            "total_balance_eth": tbe,
            "total_balance_usd": tbu,
            "net_flow_eth": nfe,
            "net_flow_usd": nfu,
            "tx_count": int(tc) if tc is not None else None,
        }
        for cat, d, ac, tbe, tbu, nfe, nfu, tc in rows
    ]


def _query_per_address(
    address: str,
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Pull per-day rows for one address."""
    sql = (
        "SELECT (ts AT TIME ZONE 'UTC')::date AS day, "
        "label, category, balance_eth, balance_usd_at_snapshot, "
        "net_flow_eth_24h, net_flow_usd_24h, tx_count_24h "
        "FROM onchain.eth_whale_flows WHERE address = %s"
    )
    params: list[Any] = [address.lower()]
    if since is not None:
        sql += " AND (ts AT TIME ZONE 'UTC')::date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND (ts AT TIME ZONE 'UTC')::date <= %s"
        params.append(until)
    sql += " ORDER BY day DESC LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "day": d.isoformat() if isinstance(d, date) else None,
            "address": address.lower(),
            "label": label,
            "category": category,
            "balance_eth": be,
            "balance_usd_at_snapshot": bu,
            "net_flow_eth_24h": nfe,
            "net_flow_usd_24h": nfu,
            "tx_count_24h": int(tc) if tc is not None else None,
        }
        for d, label, category, be, bu, nfe, nfu, tc in rows
    ]


def _format_aggregate_human(
    rows: list[dict[str, Any]], *, category_filter: Optional[str]
) -> str:
    """Render the aggregate output as a human-readable table."""
    if not rows:
        hint = ""
        if category_filter is not None:
            hint = f" for category={category_filter}"
        return (
            f"No onchain.eth_whale_flows_aggregate rows{hint}. "
            "Has `python3 -m genkei.ingest.etherscan_whale_flow` run yet?"
        )
    header = (
        f"ETH whale-flow aggregate | "
        f"{'category=' + category_filter if category_filter else 'all categories'} | "
        f"{len(rows)} row{'s' if len(rows) != 1 else ''}"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'day':<12} {'category':<11} {'addrs':>6} "
        f"{'total_eth':>14} {'net_flow_eth':>15} {'net_flow_usd_mm':>17} {'tx':>8}"
    )
    for r in rows:
        d = r["day"] or "-"
        cat = r["category"] or "-"
        ac = f"{r['address_count']:>6}" if r["address_count"] is not None else f"{'-':>6}"
        tbe = (
            f"{r['total_balance_eth']:>14,.0f}"
            if r["total_balance_eth"] is not None
            else f"{'-':>14}"
        )
        nfe = (
            f"{r['net_flow_eth']:>+15,.0f}"
            if r["net_flow_eth"] is not None
            else f"{'-':>15}"
        )
        nfu_mm = (
            f"{r['net_flow_usd'] / 1_000_000:>+17,.1f}"
            if r["net_flow_usd"] is not None
            else f"{'-':>17}"
        )
        tc = f"{r['tx_count']:>8,}" if r["tx_count"] is not None else f"{'-':>8}"
        lines.append(f"  {d:<12} {cat:<11} {ac} {tbe} {nfe} {nfu_mm} {tc}")
    lines.append("")
    lines.append(
        "  net_flow_eth = sum(incoming) - sum(outgoing) over the "
        "24h window per address, "
        "summed per category."
    )
    lines.append(
        "  Sign convention: exchange inflow = SELL pressure (users sending TO CEX); "
        "custodian inflow = staking commitment; foundation outflow = treasury monetization."
    )
    lines.append(_HORIZON_FOOTER)
    return "\n".join(lines)


def _format_per_address_human(
    rows: list[dict[str, Any]], *, address: str
) -> str:
    """Render the per-address output as a human-readable table."""
    if not rows:
        return f"No onchain.eth_whale_flows rows for address {address}."
    label = rows[0]["label"] or "?"
    category = rows[0]["category"] or "?"
    header = (
        f"ETH whale-flow | address={address} ({label}, category={category}) | "
        f"{len(rows)} row{'s' if len(rows) != 1 else ''}"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'day':<12} {'balance_eth':>14} {'flow_eth_24h':>14} "
        f"{'flow_usd_mm':>14} {'tx_count':>8}"
    )
    for r in rows:
        d = r["day"] or "-"
        be = (
            f"{r['balance_eth']:>14,.2f}"
            if r["balance_eth"] is not None
            else f"{'-':>14}"
        )
        nfe = (
            f"{r['net_flow_eth_24h']:>+14,.4f}"
            if r["net_flow_eth_24h"] is not None
            else f"{'-':>14}"
        )
        nfu_mm = (
            f"{r['net_flow_usd_24h'] / 1_000_000:>+14,.3f}"
            if r["net_flow_usd_24h"] is not None
            else f"{'-':>14}"
        )
        tc = (
            f"{r['tx_count_24h']:>8,}"
            if r["tx_count_24h"] is not None
            else f"{'-':>8}"
        )
        lines.append(f"  {d:<12} {be} {nfe} {nfu_mm} {tc}")
    lines.append("")
    lines.append(_HORIZON_FOOTER)
    return "\n".join(lines)


def whales_cmd(
    category: Annotated[
        Optional[str],
        typer.Option(
            "--category",
            "-c",
            help=(
                "Filter to one category: exchange / custodian / foundation / "
                "whale. Aliases: cex / staking / treasury."
            ),
        ),
    ] = None,
    address: Annotated[
        Optional[str],
        typer.Option(
            "--address",
            "-a",
            help=(
                "Per-address rows for one wallet (lowercased 0x... hex). "
                "Mutually exclusive with --category."
            ),
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest day (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest day (YYYY-MM-DD)."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.", min=1)] = 60,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of human table."),
    ] = False,
    list_addresses: Annotated[
        bool,
        typer.Option(
            "--list-addresses",
            help="List configured eth_whale_addresses grouped by category and exit.",
        ),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show ETH whale-address flow aggregates per category per day."""
    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if list_addresses:
        if json_out:
            typer.echo(
                json.dumps(
                    [
                        {
                            "address": e.address,
                            "label": e.label,
                            "category": e.category,
                            "notes": e.notes,
                        }
                        for e in watchlist.eth_whale_addresses
                    ],
                    indent=2,
                    default=_json_default,
                )
            )
        else:
            typer.echo(_format_address_list_human(watchlist))
        return

    if address is not None and category is not None:
        raise typer.BadParameter(
            "--address and --category are mutually exclusive — pass one or the other."
        )

    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    if address is not None:
        rows = _query_per_address(
            address, since=since_d, until=until_d, limit=limit
        )
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(_format_per_address_human(rows, address=address.lower()))
        return

    canonical_cat: Optional[str] = None
    if category is not None:
        canonical_cat = _resolve_category(category)
    rows = _query_aggregate(
        category=canonical_cat, since=since_d, until=until_d, limit=limit
    )
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    else:
        typer.echo(_format_aggregate_human(rows, category_filter=canonical_cat))
