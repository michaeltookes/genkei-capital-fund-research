"""``genkei zcash-usage`` — Zcash shielded-pool adoption (ZEC usage).

Renders the privacy-adoption signal the 2026-07-06 ZEC research decision
flagged as missing: the on-chain **shielded share of supply** and its trend,
from ``zcash.shielded_pools`` (the Zcash node's ``valuePools``, landed daily by
``genkei.ingest.zcash_usage``). The headline question is whether the shielded
share is *growing* (privacy actually being adopted) or flat/shrinking
(narrative-only) — so the default view is the per-day trend, newest first.

Usage:
  genkei zcash-usage                    shielded-share trend (newest first)
  genkei zcash-usage --by-pool          latest per-pool breakdown
  genkei zcash-usage --since 2026-07-01 --json
"""

import json
from datetime import date
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db

# The privacy pools (mirrors genkei.ingest.zcash_usage.SHIELDED_POOLS); the CLI
# derives the shielded aggregate from the stored per-pool `shielded` flag, so
# this is only used for the --by-pool human labelling.
_SHIELDED_LABEL = {True: "shielded", False: "non-shielded"}
_HORIZON_TAG = "crypto:core:primary"


def _query_trend(
    *, since: Optional[date], until: Optional[date], limit: int
) -> list[dict[str, Any]]:
    """Per-snapshot-date shielded share of supply, newest first."""
    sql = (
        "SELECT snapshot_date, "
        "SUM(chain_value_zec) FILTER (WHERE shielded) AS shielded_zec, "
        "SUM(chain_value_zec) AS total_zec, "
        "MAX(block_height) AS block_height "
        "FROM zcash.shielded_pools WHERE 1=1"
    )
    params: list[Any] = []
    if since is not None:
        sql += " AND snapshot_date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND snapshot_date <= %s"
        params.append(until)
    sql += " GROUP BY snapshot_date ORDER BY snapshot_date DESC LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for snap_date, shielded, total, height in rows:
        shielded_f = float(shielded) if shielded is not None else 0.0
        total_f = float(total) if total else 0.0
        share = (shielded_f / total_f * 100) if total_f else None
        out.append(
            {
                "snapshot_date": snap_date.isoformat() if isinstance(snap_date, date) else None,
                "shielded_zec": shielded_f,
                "total_zec": total_f,
                "shielded_share_pct": round(share, 2) if share is not None else None,
                "block_height": int(height) if height is not None else None,
                "horizon_tag": _HORIZON_TAG,
            }
        )
    return out


def _query_by_pool() -> list[dict[str, Any]]:
    """Per-pool breakdown for the latest snapshot date."""
    sql = (
        "SELECT pool, chain_value_zec, shielded, snapshot_date "
        "FROM zcash.shielded_pools "
        "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM zcash.shielded_pools) "
        "ORDER BY chain_value_zec DESC"
    )
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return [
        {
            "pool": pool,
            "chain_value_zec": float(value),
            "shielded": bool(shielded),
            "snapshot_date": snap_date.isoformat() if isinstance(snap_date, date) else None,
        }
        for pool, value, shielded, snap_date in rows
    ]


def _format_trend_human(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "No zcash.shielded_pools rows yet. Has the collector run? "
            "Try `python3 -m genkei.ingest.zcash_usage`. The series is "
            "forward-only, so it builds from the first collection day."
        )
    header = f"Zcash shielded-pool adoption | horizon={_HORIZON_TAG} | {len(rows)} snapshot(s)"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'date':<12}{'shielded_%':>12}{'shielded_M':>14}{'total_M':>12}{'block':>12}"
    )
    for r in rows:
        share = (
            f"{r['shielded_share_pct']:>11.2f}%"
            if r["shielded_share_pct"] is not None
            else f"{'-':>12}"
        )
        lines.append(
            f"  {r['snapshot_date'] or '-':<12}{share}"
            f"{r['shielded_zec'] / 1e6:>14,.3f}{r['total_zec'] / 1e6:>12,.3f}"
            f"{(r['block_height'] or 0):>12,}"
        )
    lines.append("")
    lines.append(
        "  shielded_% = (sprout+sapling+orchard) / total supply. The TREND is the "
        "signal: rising = privacy adoption; flat = narrative-only."
    )
    return "\n".join(lines)


def _format_by_pool_human(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No zcash.shielded_pools rows yet."
    snap = rows[0]["snapshot_date"]
    total = sum(r["chain_value_zec"] for r in rows) or 1.0
    header = f"Zcash value pools | {snap} | {len(rows)} pools"
    lines = [header, "-" * len(header)]
    lines.append(f"  {'pool':<14}{'ZEC':>16}{'% supply':>10}  type")
    for r in rows:
        lines.append(
            f"  {r['pool']:<14}{r['chain_value_zec']:>16,.2f}"
            f"{r['chain_value_zec'] / total * 100:>9.1f}%  {_SHIELDED_LABEL[r['shielded']]}"
        )
    return "\n".join(lines)


def zcash_usage_cmd(
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest snapshot_date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest snapshot_date (YYYY-MM-DD)."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max snapshots.", min=1)] = 60,
    by_pool: Annotated[
        bool,
        typer.Option("--by-pool", help="Latest per-pool breakdown instead of the trend."),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Zcash shielded-pool adoption: shielded share of supply + trend."""
    if by_pool:
        rows = _query_by_pool()
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(_format_by_pool_human(rows))
        return

    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    rows = _query_trend(since=since_d, until=until_d, limit=limit)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    else:
        typer.echo(_format_trend_human(rows))
