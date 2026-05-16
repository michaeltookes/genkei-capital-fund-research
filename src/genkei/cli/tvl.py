"""``genkei tvl`` — query DeFiLlama TVL time series (B-041).

Two modes, switched by which scope flag is set:

* **Chain mode** (``--chain``) — total TVL on one chain over time,
  from ``defillama.chain_tvl``. Daily resolution, full DeFiLlama
  history.
* **Protocol mode** (``--protocol``) — per-(protocol, chain) TVL over
  time from ``defillama.protocol_tvl``. As of 2026-05-16 that table
  is empty (the chain-tvl collector is wired but per-protocol
  isn't), so this mode returns an empty result with a pointer to
  ``genkei watchlist health`` until the per-protocol collector
  lands.

Default mode (no scope flag): list the chains with the most recent
TVL coverage — a quick "what's tracked" view.

Usage:
  genkei tvl                                       chains overview
  genkei tvl --chain Ethereum                      Ethereum TVL latest
  genkei tvl --chain Ethereum --since 2024-01-01   daily history
  genkei tvl --protocol aave-v3                    per-protocol (empty today)
  genkei tvl --chain Ethereum --json
"""

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

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


def _query_chain_tvl(
    chain: str,
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    sql = "SELECT ts, tvl_usd FROM defillama.chain_tvl WHERE chain = %s"
    params: list[Any] = [chain]
    if since is not None:
        sql += " AND ts >= %s"
        params.append(datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc))
    if until is not None:
        sql += " AND ts <= %s"
        params.append(datetime.combine(until, datetime.max.time(), tzinfo=timezone.utc))
    sql += " ORDER BY ts DESC LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {"ts": ts.isoformat(), "tvl_usd": float(tvl) if tvl is not None else None}
        for (ts, tvl) in rows
    ]


def _query_protocol_tvl(
    slug: str,
    *,
    chain: Optional[str],
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    sql = "SELECT ts, chain, tvl_usd FROM defillama.protocol_tvl WHERE slug = %s"
    params: list[Any] = [slug]
    if chain is not None:
        sql += " AND chain = %s"
        params.append(chain)
    if since is not None:
        sql += " AND ts >= %s"
        params.append(datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc))
    if until is not None:
        sql += " AND ts <= %s"
        params.append(datetime.combine(until, datetime.max.time(), tzinfo=timezone.utc))
    sql += " ORDER BY ts DESC, chain LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "ts": ts.isoformat(),
            "chain": ch,
            "tvl_usd": float(tvl) if tvl is not None else None,
        }
        for (ts, ch, tvl) in rows
    ]


def _query_chains_overview(*, limit: int) -> list[dict[str, Any]]:
    """List chains by most recent TVL — a 'what's tracked' overview."""
    sql = (
        "SELECT DISTINCT ON (chain) chain, ts, tvl_usd "
        "FROM defillama.chain_tvl "
        "ORDER BY chain, ts DESC"
    )
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    rows.sort(key=lambda r: float(r[2]) if r[2] is not None else 0.0, reverse=True)
    return [
        {
            "chain": ch,
            "ts": ts.isoformat() if ts is not None else None,
            "tvl_usd": float(tvl) if tvl is not None else None,
        }
        for (ch, ts, tvl) in rows[:limit]
    ]


def _format_chain_tvl_human(chain: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            f"No TVL rows for chain {chain!r} (defillama.chain_tvl). "
            "Try a different chain name (case-sensitive: 'Ethereum', "
            "'Solana', 'Bitcoin') or widen --since."
        )
    header = f"{chain} TVL ({len(rows)} row{'s' if len(rows) != 1 else ''})"
    lines = [header, "-" * len(header)]
    lines.append(f"  {'ts':<25}  {'tvl_usd':>20}")
    for r in rows:
        tvl = f"{r['tvl_usd']:>20,.0f}" if r["tvl_usd"] is not None else f"{'n/a':>20}"
        lines.append(f"  {r['ts']:<25}  {tvl}")
    return "\n".join(lines)


def _format_protocol_tvl_human(slug: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            f"No TVL rows for protocol {slug!r} (defillama.protocol_tvl). "
            "The per-protocol TVL collector is not yet active — only "
            "defillama.chain_tvl is populated today. Run `genkei watchlist "
            "health` to see source coverage; use `--chain` for chain-level TVL."
        )
    header = f"{slug} TVL ({len(rows)} row{'s' if len(rows) != 1 else ''})"
    lines = [header, "-" * len(header)]
    lines.append(f"  {'ts':<25}  {'chain':<16}  {'tvl_usd':>20}")
    for r in rows:
        tvl = f"{r['tvl_usd']:>20,.0f}" if r["tvl_usd"] is not None else f"{'n/a':>20}"
        ch = r["chain"] or "-"
        lines.append(f"  {r['ts']:<25}  {ch:<16}  {tvl}")
    return "\n".join(lines)


def _format_chains_overview_human(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "No chain TVL rows in defillama.chain_tvl. Run `genkei "
            "watchlist health` to check the DeFiLlama ingest status."
        )
    header = f"DeFiLlama chains overview ({len(rows)} chain{'s' if len(rows) != 1 else ''})"
    lines = [header, "-" * len(header), "  (latest TVL per chain, sorted desc)"]
    lines.append(f"  {'chain':<24} {'last ts':<25}  {'tvl_usd':>20}")
    for r in rows:
        tvl = f"{r['tvl_usd']:>20,.0f}" if r["tvl_usd"] is not None else f"{'n/a':>20}"
        lines.append(f"  {r['chain']:<24} {r['ts']:<25}  {tvl}")
    return "\n".join(lines)


def tvl_cmd(
    chain: Annotated[
        Optional[str],
        typer.Option("--chain", help="Chain name, e.g. Ethereum, Solana, Bitcoin."),
    ] = None,
    protocol: Annotated[
        Optional[str],
        typer.Option("--protocol", help="DeFiLlama protocol slug, e.g. aave-v3, lido."),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Start date (YYYY-MM-DD)."),
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
    # Watchlist not used for tvl today (DeFiLlama chains/protocols aren't
    # in watchlists.yml), but kept for parity with the other subcommands —
    # makes future cross-source resolution easy.
    config: Annotated[  # noqa: ARG001
        Path,
        typer.Option("--config", help="Watchlist path (unused today, reserved).", hidden=True),
    ] = Path("src/genkei/data/watchlists.yml"),
) -> None:
    """Show DeFiLlama TVL — chain (--chain), protocol (--protocol), or chains overview (default)."""
    if chain is not None and protocol is not None:
        raise typer.BadParameter("--chain and --protocol are mutually exclusive.")
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    if chain is not None:
        rows = _query_chain_tvl(chain, since=since_d, until=until_d, limit=limit)
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(_format_chain_tvl_human(chain, rows))
    elif protocol is not None:
        rows = _query_protocol_tvl(
            protocol, chain=None, since=since_d, until=until_d, limit=limit
        )
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(_format_protocol_tvl_human(protocol, rows))
    else:
        rows = _query_chains_overview(limit=limit)
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(_format_chains_overview_human(rows))
