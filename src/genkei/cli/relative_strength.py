"""``genkei relative-strength`` — crypto peer relative-strength (B-090).

Thin CLI wrapper over ``genkei.experiments.relative_strength``. The
math (and the canonical computation) lives in the Postgres view
``analytics.crypto_relative_strength``; this command shapes the
query, resolves human-friendly tickers (BTC / SOL / SUI / …) to
their coingecko_ids via the watchlist, and renders the result.

Default mode: every watchlist crypto vs BTC at the 30d window,
sorted by relative_strength_pct DESC (the most-outperforming pair
surfaces first). Override with ``--ticker`` / ``--peer`` /
``--window``.

Usage:
  genkei relative-strength                          watchlist vs BTC @ 30d
  genkei relative-strength --ticker SUI --peer SOL  SUI vs SOL across windows
  genkei relative-strength --ticker SUI --peer SOL --window 365
  genkei relative-strength --window 7 --json        all pairs at 7d as JSON
  genkei relative-strength --peer ETH --limit 5     top 5 vs ETH at 30d
"""

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    CryptoEntry,
    Watchlist,
    load_watchlist,
)
from genkei.experiments.relative_strength import (
    DEFAULT_WINDOWS,
    RelativeStrengthRow,
    load_relative_strength,
)

DEFAULT_PEER = "BTC"
DEFAULT_WINDOW_DAYS = 30


def _resolve_ticker_to_coingecko_id(
    ticker: str, watchlist: Watchlist
) -> str:
    """Map a watchlist ticker (BTC, SUI, LINK…) to its coingecko_id."""
    entry = watchlist.find_crypto(ticker)
    if entry is None:
        raise typer.BadParameter(
            f"Ticker {ticker!r} not found in the crypto watchlist. "
            "Add it under `crypto:` in watchlists.yml first."
        )
    if not entry.coingecko_id:
        raise typer.BadParameter(
            f"Ticker {ticker!r} has no coingecko_id in the watchlist."
        )
    return entry.coingecko_id


def _coingecko_id_to_ticker(
    coingecko_id: str, watchlist: Watchlist
) -> Optional[str]:
    """Reverse-lookup for output rendering. Returns None if not in crypto:."""
    for entry in watchlist.crypto:
        if entry.coingecko_id == coingecko_id:
            return entry.symbol
    return None


def _horizon_tag(entry: CryptoEntry) -> str:
    sleeve = entry.sleeve or "core"
    return f"crypto:{sleeve}:{entry.tier}"


def _crypto_asset_ids(watchlist: Watchlist) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            entry.coingecko_id for entry in watchlist.crypto if entry.coingecko_id
        )
    )


def _crypto_horizons(watchlist: Watchlist) -> dict[str, str]:
    return {
        entry.coingecko_id: _horizon_tag(entry)
        for entry in watchlist.crypto
        if entry.coingecko_id
    }


def _row_to_dict(row: RelativeStrengthRow) -> dict[str, Any]:
    return {
        "asset": row.asset,
        "peer": row.peer,
        "horizon_tag": row.horizon,
        "window_days": row.window_days,
        "asset_latest_ts": row.asset_latest_ts.isoformat() if row.asset_latest_ts else None,
        "asset_lookback_ts": row.asset_lookback_ts.isoformat() if row.asset_lookback_ts else None,
        "asset_latest_price": row.asset_latest_price,
        "asset_lookback_price": row.asset_lookback_price,
        "asset_return_pct": row.asset_return_pct,
        "peer_latest_ts": row.peer_latest_ts.isoformat() if row.peer_latest_ts else None,
        "peer_lookback_ts": row.peer_lookback_ts.isoformat() if row.peer_lookback_ts else None,
        "peer_latest_price": row.peer_latest_price,
        "peer_lookback_price": row.peer_lookback_price,
        "peer_return_pct": row.peer_return_pct,
        "relative_strength_pct": row.relative_strength_pct,
    }


def _format_table(
    rows: list[RelativeStrengthRow],
    *,
    watchlist: Watchlist,
    show_window_col: bool,
) -> str:
    if not rows:
        return (
            "No relative-strength rows match. Either the filters excluded "
            "everything or analytics.crypto_relative_strength is empty "
            "(check `genkei watchlist health`)."
        )
    if show_window_col:
        header = (
            f"  {'asset':<10} {'peer':<10} {'horizon':<24} {'window':>7} "
            f"{'asset%':>10} {'peer%':>10} {'rel_str%':>10}  as_of"
        )
    else:
        header = (
            f"  {'asset':<10} {'peer':<10} {'horizon':<24} "
            f"{'asset%':>10} {'peer%':>10} {'rel_str%':>10}  as_of"
        )
    lines = [header, "-" * len(header)]
    for r in rows:
        asset_ticker = _coingecko_id_to_ticker(r.asset, watchlist) or r.asset
        peer_ticker = _coingecko_id_to_ticker(r.peer, watchlist) or r.peer
        asset_pct = _fmt_pct(r.asset_return_pct)
        peer_pct = _fmt_pct(r.peer_return_pct)
        rel_pct = _fmt_pct(r.relative_strength_pct)
        as_of = r.asset_latest_ts.isoformat() if r.asset_latest_ts else "-"
        if show_window_col:
            lines.append(
                f"  {asset_ticker:<10} {peer_ticker:<10} {r.horizon:<24} "
                f"{r.window_days:>6}d "
                f"{asset_pct:>10} {peer_pct:>10} {rel_pct:>10}  {as_of}"
            )
        else:
            lines.append(
                f"  {asset_ticker:<10} {peer_ticker:<10} {r.horizon:<24} "
                f"{asset_pct:>10} {peer_pct:>10} {rel_pct:>10}  {as_of}"
            )
    return "\n".join(lines)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):+.1f}%"


def relative_strength_cmd(
    ticker: Annotated[
        Optional[str],
        typer.Option(
            "--ticker",
            "-t",
            help="Crypto watchlist ticker (BTC, SUI, …). Filters asset side.",
        ),
    ] = None,
    peer: Annotated[
        Optional[str],
        typer.Option(
            "--peer",
            "-p",
            help=(
                "Peer crypto watchlist ticker (default BTC unless --ticker "
                "is also set, in which case all windows are shown for the pair)."
            ),
        ),
    ] = None,
    window: Annotated[
        Optional[int],
        typer.Option(
            "--window",
            "-w",
            help=(
                f"Trailing window in days. Default {DEFAULT_WINDOW_DAYS}d unless "
                "--ticker and --peer are both set, in which case all 5 windows show."
            ),
            min=1,
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max rows to return.", min=1),
    ] = 50,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of human table."),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show crypto peer relative-strength from analytics.crypto_relative_strength."""
    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    asset_id: Optional[str] = None
    peer_id: Optional[str] = None
    if ticker is not None:
        asset_id = _resolve_ticker_to_coingecko_id(ticker, watchlist)
    if peer is not None:
        peer_id = _resolve_ticker_to_coingecko_id(peer, watchlist)

    # Default-mode shape: every watchlist crypto vs BTC at 30d. If
    # --ticker is set without --peer, default peer is BTC. If both
    # --ticker and --peer are set, show all windows for that pair
    # unless --window narrows it.
    if peer_id is None and asset_id is None:
        peer_id = _resolve_ticker_to_coingecko_id(DEFAULT_PEER, watchlist)
    if peer_id is None and asset_id is not None:
        peer_id = _resolve_ticker_to_coingecko_id(DEFAULT_PEER, watchlist)
    asset_ids: Optional[tuple[str, ...]] = None
    if asset_id is None:
        asset_ids = _crypto_asset_ids(watchlist)

    if window is not None:
        window_filter: Optional[int] = window
    elif asset_id is not None and peer is not None:
        # Specific pair, no window override → show all 5
        window_filter = None
    else:
        window_filter = DEFAULT_WINDOW_DAYS

    if window_filter is not None and window_filter not in DEFAULT_WINDOWS:
        # The view only carries the 5 default windows; warn rather than
        # silently returning empty.
        raise typer.BadParameter(
            f"--window must be one of {list(DEFAULT_WINDOWS)}; got {window_filter}."
        )

    load_kwargs: dict[str, Any] = {
        "asset": asset_id,
        "peer": peer_id,
        "window_days": window_filter,
        "limit": limit,
        "asset_horizons": _crypto_horizons(watchlist),
    }
    if asset_ids is not None:
        load_kwargs["assets"] = asset_ids
    rows = load_relative_strength(**load_kwargs)

    if json_out:
        typer.echo(
            json.dumps([_row_to_dict(r) for r in rows], indent=2, default=_json_default)
        )
        return

    show_window = window_filter is None
    typer.echo(_format_table(rows, watchlist=watchlist, show_window_col=show_window))
