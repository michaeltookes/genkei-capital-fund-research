"""``genkei top-ranks`` — daily top-N market-cap rank snapshot (B-149).

Reads ``coingecko.market_rank``, the forward-only table the CoinGecko daily run
fills from one ``/coins/markets`` call (top 50 by market cap). It exists to grade
the "top 15 is essentially chosen" thesis: ``--diff`` shows which coins entered
or left the top N between two snapshots and who moved.

Usage:
  genkei top-ranks                         latest snapshot, top 15
  genkei top-ranks --n 10 --date 2026-10-29
  genkei top-ranks --exclude-stables       drop watchlist ``stablecoin_ids`` and
                                           re-rank the rest (``adj_rank``)
  genkei top-ranks --diff 2026-10-29 2027-01-08 --n 15
                                           entries / exits / rank moves, --json too

The series is forward-only (the free CoinGecko tier has no historical rank
endpoint), so a date before the first captured run has no snapshot.
"""

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist

_COLUMNS = "coingecko_id, symbol, rank, market_cap_usd, price_usd, volume_usd, fully_diluted_usd"


def _num(value: Any) -> Optional[float]:
    return float(value) if value is not None else None


def _fetch_snapshots(dates: list[date]) -> dict[date, list[dict[str, Any]]]:
    """Return ``{ts: rows ordered by rank}`` for the requested dates."""
    sql = f"SELECT ts, {_COLUMNS} FROM coingecko.market_rank WHERE ts = ANY(%s) ORDER BY ts, rank"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, [dates])
        fetched = cur.fetchall()
    out: dict[date, list[dict[str, Any]]] = {d: [] for d in dates}
    for ts, cgid, symbol, rank, mcap, price, volume, fdv in fetched:
        out.setdefault(ts, []).append(
            {
                "coingecko_id": cgid,
                "symbol": symbol,
                "rank": int(rank),
                "market_cap_usd": _num(mcap),
                "price_usd": _num(price),
                "volume_usd": _num(volume),
                "fully_diluted_usd": _num(fdv),
            }
        )
    return out


def _latest_date() -> Optional[date]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(ts) FROM coingecko.market_rank", [])
        row = cur.fetchone()
    return row[0] if row else None


def _rank_all(
    rows: list[dict[str, Any]], *, stable_ids: frozenset[str], exclude_stables: bool
) -> list[dict[str, Any]]:
    """Rows ordered by rank, each with ``adj_rank``.

    With ``exclude_stables`` the stablecoins drop out and the survivors are
    re-numbered 1..k; otherwise ``adj_rank`` equals the raw rank, so downstream
    code reads a single field either way.
    """
    kept = sorted(
        (r for r in rows if not (exclude_stables and r["coingecko_id"] in stable_ids)),
        key=lambda r: r["rank"],
    )
    if exclude_stables:
        return [{**r, "adj_rank": i} for i, r in enumerate(kept, start=1)]
    return [{**r, "adj_rank": r["rank"]} for r in kept]


def build_snapshot(
    rows: list[dict[str, Any]],
    *,
    ts: Optional[date],
    n: int,
    stable_ids: frozenset[str],
    exclude_stables: bool,
) -> dict[str, Any]:
    top = _rank_all(rows, stable_ids=stable_ids, exclude_stables=exclude_stables)[:n]
    return {
        "date": ts,
        "n": n,
        "exclude_stables": exclude_stables,
        "rows": [{**r, "is_stable": r["coingecko_id"] in stable_ids} for r in top],
    }


def build_diff(
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    *,
    date_a: date,
    date_b: date,
    n: int,
    stable_ids: frozenset[str],
    exclude_stables: bool,
) -> dict[str, Any]:
    """Compare the top ``n`` of two snapshots: entries, exits, rank moves.

    Entries/exits are membership changes in the top ``n`` (by ``adj_rank``).
    A coin in both top-``n`` sets whose rank differs is a move. A coin that
    left the top n but is still in the stored top 50 reports its new rank;
    ``None`` means it is outside the stored top 50 that day.

    Missing source snapshots make the comparison unavailable. Keep the
    comparison lists empty and report the missing dates before filtering.
    """
    result: dict[str, Any] = {
        "date_a": date_a,
        "date_b": date_b,
        "n": n,
        "exclude_stables": exclude_stables,
        "entries": [],
        "exits": [],
        "moves": [],
        "unchanged": 0,
        "missing_dates": [d for d, rows in ((date_a, rows_a), (date_b, rows_b)) if not rows],
    }
    if result["missing_dates"]:
        return result

    full_a = {
        r["coingecko_id"]: r
        for r in _rank_all(rows_a, stable_ids=stable_ids, exclude_stables=exclude_stables)
    }
    full_b = {
        r["coingecko_id"]: r
        for r in _rank_all(rows_b, stable_ids=stable_ids, exclude_stables=exclude_stables)
    }
    top_a = {c: r for c, r in full_a.items() if r["adj_rank"] <= n}
    top_b = {c: r for c, r in full_b.items() if r["adj_rank"] <= n}

    def _side(cgid: str, symbol: str) -> dict[str, Any]:
        a, b = full_a.get(cgid), full_b.get(cgid)
        return {
            "coingecko_id": cgid,
            "symbol": symbol,
            "rank_a": a["adj_rank"] if a else None,
            "rank_b": b["adj_rank"] if b else None,
            "market_cap_usd_a": a["market_cap_usd"] if a else None,
            "market_cap_usd_b": b["market_cap_usd"] if b else None,
        }

    far = 10**6  # sort key for "outside the stored top 50"
    entries = sorted(
        (_side(c, r["symbol"]) for c, r in top_b.items() if c not in top_a),
        key=lambda x: x["rank_b"],
    )
    exits = sorted(
        (_side(c, r["symbol"]) for c, r in top_a.items() if c not in top_b),
        key=lambda x: x["rank_b"] if x["rank_b"] is not None else far,
    )
    moves = sorted(
        (
            {**_side(c, r["symbol"]), "change": r["adj_rank"] - top_b[c]["adj_rank"]}
            for c, r in top_a.items()
            if c in top_b and r["adj_rank"] != top_b[c]["adj_rank"]
        ),
        key=lambda x: x["rank_b"],
    )
    unchanged = sum(
        1 for c, r in top_a.items() if c in top_b and r["adj_rank"] == top_b[c]["adj_rank"]
    )
    return {
        **result,
        "entries": entries,
        "exits": exits,
        "moves": moves,
        "unchanged": unchanged,
    }


def _fmt_usd(value: Optional[float]) -> str:
    if value is None:
        return f"{'n/a':>10}"
    if abs(value) >= 1e9:
        return f"{value / 1e9:>9.2f}B"
    if abs(value) >= 1e6:
        return f"{value / 1e6:>9.2f}M"
    return f"{value:>10,.0f}"


def _fmt_rank(value: Optional[int]) -> str:
    return f"{value:>4}" if value is not None else "   -"


def format_snapshot_human(snap: dict[str, Any]) -> str:
    if not snap["rows"]:
        when = f" for {snap['date']}" if snap["date"] else ""
        return (
            f"No market-rank snapshot{when}. The series is forward-only: it starts "
            "with the first CoinGecko daily run after the B-149 migration."
        )
    suffix = ", stablecoins excluded" if snap["exclude_stables"] else ""
    header = f"Top {len(snap['rows'])} by market cap | {snap['date']}{suffix}"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'#':>3}{'raw':>5}  {'symbol':<8}{'mcap':>11}{'price':>14}{'24h vol':>11}  note"
    )
    for r in snap["rows"]:
        price = f"{r['price_usd']:>14,.4f}" if r["price_usd"] is not None else f"{'n/a':>14}"
        note = "stable" if r["is_stable"] else ""
        lines.append(
            f"  {r['adj_rank']:>3}{r['rank']:>5}  {r['symbol']:<8}"
            f"{_fmt_usd(r['market_cap_usd']):>11}{price}{_fmt_usd(r['volume_usd']):>11}  {note}"
        )
    return "\n".join(lines)


def format_diff_human(diff: dict[str, Any]) -> str:
    suffix = ", stablecoins excluded" if diff["exclude_stables"] else ""
    header = f"Top {diff['n']} rank diff | {diff['date_a']} -> {diff['date_b']}{suffix}"
    lines = [header, "-" * len(header)]
    if diff["missing_dates"]:
        missing = ", ".join(str(d) for d in diff["missing_dates"])
        lines.append(
            f"  No snapshot for: {missing}. The series is forward-only; "
            "pick dates on or after the first captured run."
        )
        return "\n".join(lines)
    if not (diff["entries"] or diff["exits"] or diff["moves"]):
        lines.append(f"  No change: the same {diff['n']} coins in the same order.")
        return "\n".join(lines)

    def _row(e: dict[str, Any], extra: str = "") -> str:
        return f"    {e['symbol']:<8}{_fmt_rank(e['rank_a'])} ->{_fmt_rank(e['rank_b'])}{extra}"

    lines.append(f"  Entered ({len(diff['entries'])}):")
    lines.extend(_row(e) for e in diff["entries"])
    lines.append(f"  Exited ({len(diff['exits'])}):")
    lines.extend(_row(e) for e in diff["exits"])
    lines.append(
        f"  Moved within top {diff['n']} ({len(diff['moves'])}), unchanged {diff['unchanged']}:"
    )
    lines.extend(_row(m, f"  ({m['change']:+d})") for m in diff["moves"])
    lines.append("")
    lines.append("  '-' = outside the stored top 50 on that date. change > 0 = moved up.")
    return "\n".join(lines)


def top_ranks_cmd(
    n: Annotated[int, typer.Option("--n", help="Top N coins to show / compare.", min=1)] = 15,
    on_date: Annotated[
        Optional[str],
        typer.Option("--date", help="Snapshot date YYYY-MM-DD (default: latest)."),
    ] = None,
    exclude_stables: Annotated[
        bool,
        typer.Option(
            "--exclude-stables",
            help="Drop watchlist stablecoin_ids and re-rank the remainder.",
        ),
    ] = False,
    diff: Annotated[
        Optional[tuple[str, str]],
        typer.Option("--diff", help="Compare two snapshots: --diff YYYY-MM-DD YYYY-MM-DD."),
    ] = None,
    diff_from: Annotated[
        Optional[str],
        typer.Option("--diff-from", help="Single-value form of --diff (start date).", hidden=True),
    ] = None,
    diff_to: Annotated[
        Optional[str],
        typer.Option("--diff-to", help="Single-value form of --diff (end date).", hidden=True),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
    config: Annotated[
        Path, typer.Option("--config", help="Watchlist path.", show_default=True)
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Daily top-N market-cap rank snapshot, or a diff between two dates."""
    if (diff_from is None) != (diff_to is None):
        raise typer.BadParameter("--diff-from and --diff-to must be given together.")
    if diff is None and diff_from is not None and diff_to is not None:
        diff = (diff_from, diff_to)
    if diff is not None and on_date is not None:
        raise typer.BadParameter("--date and --diff are mutually exclusive.")
    try:
        stable_ids = frozenset(load_watchlist(config).stablecoin_ids)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    result: dict[str, Any]
    if diff is not None:
        date_a = _parse_date(diff[0], label="diff")
        date_b = _parse_date(diff[1], label="diff")
        assert date_a is not None and date_b is not None
        snaps = _fetch_snapshots([date_a, date_b])
        result = build_diff(
            snaps.get(date_a, []),
            snaps.get(date_b, []),
            date_a=date_a,
            date_b=date_b,
            n=n,
            stable_ids=stable_ids,
            exclude_stables=exclude_stables,
        )
        text = format_diff_human(result)
    else:
        ts = _parse_date(on_date, label="date") or _latest_date()
        rows = _fetch_snapshots([ts]).get(ts, []) if ts is not None else []
        result = build_snapshot(
            rows, ts=ts, n=n, stable_ids=stable_ids, exclude_stables=exclude_stables
        )
        text = format_snapshot_human(result)

    if json_out:
        typer.echo(json.dumps(result, indent=2, default=_json_default))
    else:
        typer.echo(text)
