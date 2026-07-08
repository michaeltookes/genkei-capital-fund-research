"""``genkei anomalies`` — per-series statistical-outlier flags (B-069).

Reads ``meta.anomalies``, the flags landed by the anomaly detector
(``genkei.experiments.emitters.anomaly_emitter``): observations whose daily
return was a rolling robust outlier (MAD-based modified z-score, Iglewicz–
Hoaglin). The default view is the most-recent flags across every asset; filter
by ``--asset`` / ``--asset-class`` / ``--direction`` to focus.

Usage:
  genkei anomalies                          most-recent flags, all assets
  genkei anomalies --asset NVDA             one asset's history
  genkei anomalies --asset-class crypto --since 2026-01-01
  genkei anomalies --direction spike_down --min-score 5 --json
"""

import json
from datetime import date
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist

_VALID_CLASSES = {"crypto", "equity"}
_VALID_DIRECTIONS = {"spike_up", "spike_down"}


def _append_unique(values: list[str], value: str) -> None:
    stripped = value.strip()
    if stripped and stripped not in values:
        values.append(stripped)


def _asset_filter_values(asset: Optional[str]) -> list[str]:
    """Return stored asset IDs that should satisfy a user-facing asset filter."""
    if asset is None:
        return []
    stripped = asset.strip()
    if not stripped:
        return []
    values = [stripped]
    try:
        watchlist = load_watchlist(DEFAULT_WATCHLIST_PATH)
    except (FileNotFoundError, ValueError):
        return values

    crypto = watchlist.find_crypto(stripped)
    if crypto is not None:
        _append_unique(values, crypto.symbol.upper())
        _append_unique(values, crypto.coingecko_id)

    equity = watchlist.find_equity(stripped)
    if equity is not None:
        _append_unique(values, equity.symbol.upper())

    return values


def _query(
    *,
    asset: Optional[str],
    asset_class: Optional[str],
    direction: Optional[str],
    since: Optional[date],
    until: Optional[date],
    min_score: Optional[float],
    limit: int,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT asset, asset_class, metric, ts::date AS d, value, score, method, "
        "direction, window_days, threshold, median, mad "
        "FROM meta.anomalies WHERE 1=1"
    )
    params: list[Any] = []
    asset_values = _asset_filter_values(asset)
    if asset_values:
        placeholders = ", ".join(["%s"] * len(asset_values))
        sql += f" AND asset IN ({placeholders})"
        params.extend(asset_values)
    if asset_class is not None:
        sql += " AND asset_class = %s"
        params.append(asset_class)
    if direction is not None:
        sql += " AND direction = %s"
        params.append(direction)
    if since is not None:
        sql += " AND ts::date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND ts::date <= %s"
        params.append(until)
    if min_score is not None:
        sql += " AND abs(score) >= %s"
        params.append(min_score)
    # Newest first, then strongest — the "what just fired, worst first" default.
    sql += " ORDER BY d DESC, abs(score) DESC LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for asset_, cls, metric, d, value, score, method, dirn, window, thr, median, mad in rows:
        out.append(
            {
                "asset": asset_,
                "asset_class": cls,
                "metric": metric,
                "date": d.isoformat() if isinstance(d, date) else None,
                "value": float(value),
                "score": float(score),
                "method": method,
                "direction": dirn,
                "window_days": int(window),
                "threshold": float(thr),
                "median": float(median) if median is not None else None,
                "mad": float(mad) if mad is not None else None,
            }
        )
    return out


def _format_human(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "No anomalies match. Has the detector run? Try "
            "`python3 -m genkei.experiments.emitters.anomaly_emitter`. "
            "Only observations past the outlier threshold are stored, so an "
            "empty result can simply mean no asset had an unusual move."
        )
    header = f"Return anomalies | {len(rows)} flag(s) | newest first"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'asset':<14}{'class':<7}{'date':<12}{'return_%':>10}{'score':>8}"
        f"  {'direction':<13}method"
    )
    for r in rows:
        arrow = "▲" if r["direction"] == "spike_up" else "▼"
        lines.append(
            f"  {r['asset']:<14}{r['asset_class']:<7}{r['date'] or '-':<12}"
            f"{r['value'] * 100:>9.2f}%{r['score']:>8.2f}"
            f"  {arrow + ' ' + r['direction']:<13}{r['method']}"
        )
    lines.append("")
    lines.append(
        "  score = signed modified z-score (MAD-based) of the day's return vs its "
        "trailing window; |score| ≥ threshold flags it."
    )
    return "\n".join(lines)


def anomalies_cmd(
    asset: Annotated[
        Optional[str],
        typer.Option("--asset", help="Filter to one asset (ticker or coingecko_id)."),
    ] = None,
    asset_class: Annotated[
        Optional[str],
        typer.Option("--asset-class", help="Filter to 'crypto' or 'equity'."),
    ] = None,
    direction: Annotated[
        Optional[str],
        typer.Option("--direction", help="Filter to 'spike_up' or 'spike_down'."),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest anomaly date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest anomaly date (YYYY-MM-DD)."),
    ] = None,
    min_score: Annotated[
        Optional[float],
        typer.Option("--min-score", help="Only flags with |score| at or above this."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max flags.", min=1)] = 50,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Per-series return anomalies (rolling MAD-based outlier flags)."""
    if asset_class is not None and asset_class not in _VALID_CLASSES:
        raise typer.BadParameter("--asset-class must be 'crypto' or 'equity'.")
    if direction is not None and direction not in _VALID_DIRECTIONS:
        raise typer.BadParameter("--direction must be 'spike_up' or 'spike_down'.")
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    rows = _query(
        asset=asset,
        asset_class=asset_class,
        direction=direction,
        since=since_d,
        until=until_d,
        min_score=min_score,
        limit=limit,
    )
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    else:
        typer.echo(_format_human(rows))
