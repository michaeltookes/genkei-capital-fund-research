"""``genkei momentum`` — trailing 3/7/30-day price momentum (B-067).

Reads ``analytics.price_momentum``, the materialized view that precomputes each
watchlist asset's trailing 3-day / 7-day / 30-day returns (crypto from
``coinbase.candles``, equity from ``yahoo.candles`` adj-close), refreshed daily
by ``genkei.experiments.refresh_price_momentum``. The point of the matview is
that this read is a single indexed scan — no per-call recomputation.

Usage:
  genkei momentum                          all assets, strongest 7d first
  genkei momentum --asset-class crypto     just the crypto sleeve
  genkei momentum --asset BTC              one asset
  genkei momentum --window 30 --json       sort by 30d return, machine-readable
"""

import json
from datetime import date
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.common import db

_VALID_CLASSES = {"crypto", "equity"}
_WINDOW_COLUMN = {3: "ret_3d", 7: "ret_7d", 30: "ret_30d"}


def _query(
    *,
    asset: Optional[str],
    asset_class: Optional[str],
    sort_window: int,
    limit: int,
) -> list[dict[str, Any]]:
    sort_col = _WINDOW_COLUMN[sort_window]
    sql = (
        "SELECT asset, asset_class, ts, close, ret_3d, ret_7d, ret_30d "
        "FROM analytics.price_momentum WHERE 1=1"
    )
    params: list[Any] = []
    if asset is not None:
        sql += " AND asset = %s"
        params.append(asset.upper())
    if asset_class is not None:
        sql += " AND asset_class = %s"
        params.append(asset_class)
    # NULLS LAST so assets without enough history for the sort window sink to
    # the bottom rather than masquerading as the weakest movers.
    sql += f" ORDER BY {sort_col} DESC NULLS LAST, asset LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for asset_, cls, ts, close, r3, r7, r30 in rows:
        out.append(
            {
                "asset": asset_,
                "asset_class": cls,
                "ts": ts.isoformat() if isinstance(ts, date) else None,
                "close": float(close) if close is not None else None,
                "ret_3d": float(r3) if r3 is not None else None,
                "ret_7d": float(r7) if r7 is not None else None,
                "ret_30d": float(r30) if r30 is not None else None,
            }
        )
    return out


def _fmt_pct(value: Optional[float]) -> str:
    return f"{value:>+8.2f}%" if value is not None else f"{'n/a':>9}"


def _format_human(rows: list[dict[str, Any]], *, sort_window: int) -> str:
    if not rows:
        return (
            "No momentum rows match. Has the matview been refreshed? Try "
            "`python3 -m genkei.experiments.refresh_price_momentum`. It covers "
            "watchlist crypto (coinbase) + equity (yahoo) close series."
        )
    header = (
        f"Price momentum | {len(rows)} asset(s) | sorted by {sort_window}d return"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'asset':<8}{'class':<8}{'date':<12}{'close':>14}"
        f"{'3d':>9}{'7d':>9}{'30d':>9}"
    )
    for r in rows:
        close = f"{r['close']:>14,.4f}" if r["close"] is not None else f"{'n/a':>14}"
        lines.append(
            f"  {r['asset']:<8}{r['asset_class']:<8}{r['ts'] or '-':<12}{close}"
            f"{_fmt_pct(r['ret_3d'])}{_fmt_pct(r['ret_7d'])}{_fmt_pct(r['ret_30d'])}"
        )
    lines.append("")
    lines.append(
        "  ret_Nd = trailing N-day return (calendar days, most recent close "
        "at/before the lookback). n/a = insufficient history for that window."
    )
    return "\n".join(lines)


def momentum_cmd(
    asset: Annotated[
        Optional[str],
        typer.Option("--asset", help="Filter to one asset (symbol, e.g. BTC / AAPL)."),
    ] = None,
    asset_class: Annotated[
        Optional[str],
        typer.Option("--asset-class", help="Filter to 'crypto' or 'equity'."),
    ] = None,
    window: Annotated[
        int,
        typer.Option("--window", help="Sort by this window's return (3, 7, or 30)."),
    ] = 7,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.", min=1)] = 50,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Trailing 3/7/30-day price momentum across the watchlist."""
    if asset_class is not None and asset_class not in _VALID_CLASSES:
        raise typer.BadParameter("--asset-class must be 'crypto' or 'equity'.")
    if window not in _WINDOW_COLUMN:
        raise typer.BadParameter("--window must be one of 3, 7, 30.")
    rows = _query(
        asset=asset,
        asset_class=asset_class,
        sort_window=window,
        limit=limit,
    )
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    else:
        typer.echo(_format_human(rows, sort_window=window))
