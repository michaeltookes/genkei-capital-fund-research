"""``genkei stablecoin-flow`` — cross-chain stablecoin supply trajectory (B-108).

Promotes the cross-chain capital-flow signal that yesterday's 2026-06-03
BTC/ETH/SOL comparative research decision named as *"the strongest
single cross-asset comparative signal in the lake right now"* from an
SQL escape hatch (``genkei query "SELECT … FROM defillama.stablecoins
…"``) to a typed CLI surface. Pure query layer over the already-flowing
``defillama.stablecoins`` table — zero new external dependencies.

Modes
-----

  * ``--chain Ethereum --since 2026-01-01`` — single-chain daily
    trajectory with rolling 7d / 30d ``Δ`` columns so the "are stables
    flowing IN or OUT" question reads directly off the table without
    further math.
  * ``--all-chains`` — comparative snapshot across chains with material
    stablecoin presence (default ≥ $0.5B). Latest-day supply plus 7d / 30d
    deltas per chain, sorted by current supply. The "is capital rotating
    between chains" question becomes one command.
  * ``--chain Ethereum --by-stablecoin`` — per-asset split within the
    chain (USDT, USDC, DAI, …) for the latest day. Useful for detecting
    single-asset-driven flow effects (an MMF redemption hitting Ethereum
    that doesn't reflect broader sentiment).

The output column is honestly named ``supply_usd_b`` / ``delta_7d_usd_b``
/ ``delta_30d_usd_b`` — these are *circulating supply* and absolute
*supply deltas*, NOT signed creation/redemption flows from primary
sources. defillama aggregates from on-chain readers; the delta reads as
"net new supply minted on this chain over the window" which is the right
proxy for "is capital arriving/leaving on this chain", but not the
identical thing.

Usage
-----

::

   genkei stablecoin-flow --chain Ethereum                       latest 30d trajectory
   genkei stablecoin-flow --chain Ethereum --since 2026-01-01
   genkei stablecoin-flow --all-chains                           current snapshot
   genkei stablecoin-flow --chain Ethereum --by-stablecoin       per-asset split
   genkei stablecoin-flow --chain Ethereum --json
   genkei stablecoin-flow --list-chains                          list chains with data

Chain aliases accepted: ``eth`` / ``ethereum``; ``sol`` / ``solana``;
``btc`` / ``bitcoin``; ``tron`` / ``trx``; ``bsc`` / ``bnb``;
``arb`` / ``arbitrum``; ``polygon`` / ``matic``; ``avalanche`` / ``avax``.
Case-insensitive — ``ethereum`` and ``Ethereum`` both resolve.
"""

import json
from datetime import date, datetime, timezone
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import (
    json_default as _json_default,
)
from genkei.cli._helpers import (
    parse_date as _parse_date,
)
from genkei.common import db

# Canonical chain names match the values defillama publishes in
# ``defillama.stablecoins.chain`` (verified against the live table).
# Aliases map any caller-friendly form (case-insensitive) to those
# canonical values. The chain set is *not* watchlist-driven because
# stablecoin supply is meaningful on chains that aren't in any other
# watchlist section (Tron is the second-largest stablecoin chain and
# isn't a research target on its own).
_CHAIN_ALIASES: dict[str, str] = {
    "eth": "Ethereum",
    "ethereum": "Ethereum",
    "sol": "Solana",
    "solana": "Solana",
    "btc": "Bitcoin",
    "bitcoin": "Bitcoin",
    "tron": "Tron",
    "trx": "Tron",
    "bsc": "BSC",
    "bnb": "BSC",
    "arb": "Arbitrum",
    "arbitrum": "Arbitrum",
    "base": "Base",
    "polygon": "Polygon",
    "matic": "Polygon",
    "avalanche": "Avalanche",
    "avax": "Avalanche",
    "hyperliquid": "Hyperliquid L1",
    "hyperliquid l1": "Hyperliquid L1",
    "aptos": "Aptos",
    "ton": "TON",
    "stellar": "Stellar",
    "xrpl": "XRPL",
}

# Default window for trajectory mode when --since isn't passed. 30 days
# is enough to compute the rolling 30d delta on day 1 (with the prior
# 30d as the "look-back" window pulled implicitly by the SQL).
DEFAULT_TRAJECTORY_DAYS = 30

# Default minimum chain supply for --all-chains. Filters the long tail
# of chains with <$0.5B in stables (defillama tracks chains with as
# little as $10k); below this threshold the chain isn't a meaningful
# institutional-flow target.
DEFAULT_MIN_SUPPLY_B = 0.5


def _default_since_date(today: Optional[date] = None) -> date:
    """Return the inclusive lower bound for the default trajectory window."""
    anchor = today or date.today()
    return date.fromordinal(anchor.toordinal() - (DEFAULT_TRAJECTORY_DAYS - 1))


def _resolve_chain(raw: str) -> str:
    """Return the canonical defillama chain name for a user-provided alias.

    Falls through for chain names that aren't aliased (so the user can
    pass ``Optimism`` even though we don't have an alias for it) — the
    SQL query will then return zero rows if the chain doesn't exist,
    which surfaces as a clear "no rows" hint downstream.
    """
    stripped = raw.strip() if raw else ""
    if not stripped:
        raise typer.BadParameter("--chain must be a non-empty string.")
    key = stripped.lower()
    if key in _CHAIN_ALIASES:
        return _CHAIN_ALIASES[key]
    # Preserve exact mixed-case/acronym chain names copied from --list-chains.
    # Pure lowercase unknowns still get the historical TitleCase convenience.
    return stripped.title() if stripped.islower() else stripped


def _horizon_tag(chain: str) -> str:
    return f"stablecoin:{chain.lower().replace(' ', '_')}"


def _tag_rows(rows: list[dict[str, Any]], horizon_tag: str) -> list[dict[str, Any]]:
    return [{**row, "horizon_tag": horizon_tag} for row in rows]


def _validate_mode_flags(
    *,
    list_chains: bool,
    all_chains: bool,
    chain: Optional[str],
    by_stablecoin: bool,
    since: Optional[str],
    until: Optional[str],
) -> None:
    """Reject CLI mode combinations that would otherwise be silently ignored."""
    if list_chains and any(
        (
            all_chains,
            chain is not None,
            by_stablecoin,
            since is not None,
            until is not None,
        )
    ):
        raise typer.BadParameter(
            "--list-chains cannot be combined with --chain, --all-chains, "
            "--by-stablecoin, --since, or --until."
        )
    if all_chains and any(
        (chain is not None, by_stablecoin, since is not None, until is not None)
    ):
        raise typer.BadParameter(
            "--all-chains cannot be combined with --chain, --by-stablecoin, "
            "--since, or --until."
        )
    if by_stablecoin and (since is not None or until is not None):
        raise typer.BadParameter("--by-stablecoin does not support --since/--until.")


def _to_float(value: Any) -> Optional[float]:
    """Decimal / int / None → float / None for clean JSON + format."""
    if value is None:
        return None
    return float(value)


def _query_chain_trajectory(
    chain: str,
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Daily supply rows for one chain with rolling 7d / 30d deltas.

    The CTE computes the daily aggregate first (one row per day per
    chain even though the upstream rows are per-stablecoin), then
    applies ``LAG`` window functions ordered by day to compute the
    deltas in a single pass. The ``--since`` filter is applied *after*
    the LAG so the 7d / 30d windows correctly span across the boundary
    (a query with ``--since 2026-06-01`` still gets a 7d delta on
    2026-06-01 by looking back to 2026-05-25 internally).
    """
    where = "chain = %s"
    params: list[Any] = [chain]
    # Add a 35-day lookback buffer so the LAG(30) on the first
    # returned row has data to look back to. Without this the first
    # 30 days of the window show NULL for delta_30d.
    lookback_days = 35
    if since is not None:
        where += " AND ts >= %s"
        params.append(
            datetime.combine(
                date.fromordinal(since.toordinal() - lookback_days),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        )
    if until is not None:
        where += " AND ts <= %s"
        params.append(
            datetime.combine(until, datetime.max.time(), tzinfo=timezone.utc)
        )

    # B-109 retired the per-query DISTINCT ON workaround on 2026-06-04
    # via the normalize-layer day-align fix + the dedupe migration.
    # ts is now canonical UTC-midnight per day and the (asset_id, chain, ts)
    # PK enforces per-day uniqueness, so SUM(supply_usd) GROUP BY ts::date
    # produces the correct value directly.
    sql = f"""
        WITH daily AS (
            SELECT ts::date AS day, SUM(supply_usd) / 1e9 AS supply_b
            FROM defillama.stablecoins
            WHERE {where}
            GROUP BY day
        ),
        with_deltas AS (
            SELECT day, supply_b,
                   supply_b - LAG(supply_b, 7) OVER (ORDER BY day) AS delta_7d_b,
                   supply_b - LAG(supply_b, 30) OVER (ORDER BY day) AS delta_30d_b
            FROM daily
        )
        SELECT day, supply_b, delta_7d_b, delta_30d_b
        FROM with_deltas
    """
    # Apply the user-visible --since filter to the OUTPUT (after the
    # lookback buffer has done its job inside the CTE).
    if since is not None:
        sql += " WHERE day >= %s"
        params.append(since)
    sql += " ORDER BY day DESC LIMIT %s"
    params.append(limit)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for day, supply_b, delta_7d, delta_30d in rows:
        out.append(
            {
                "chain": chain,
                "day": day.isoformat() if isinstance(day, date) else None,
                "supply_usd_b": _to_float(supply_b),
                "delta_7d_usd_b": _to_float(delta_7d),
                "delta_30d_usd_b": _to_float(delta_30d),
            }
        )
    return out


def _query_all_chains_snapshot(
    *,
    min_supply_b: float,
    limit: int,
) -> list[dict[str, Any]]:
    """Latest-day supply + 7d / 30d Δ per chain, filtered to material chains."""
    # B-109 retired the DISTINCT ON workaround (see _query_chain_trajectory).
    # Direct SUM aggregation is correct now that ts is day-aligned + the
    # PK enforces uniqueness.
    sql = """
        WITH latest_day AS (
            SELECT MAX(ts::date) AS day
            FROM defillama.stablecoins
        ),
        daily AS (
            SELECT chain, ts::date AS day, SUM(supply_usd) / 1e9 AS supply_b
            FROM defillama.stablecoins
            WHERE ts::date >= (SELECT day FROM latest_day) - 60
            GROUP BY chain, day
        ),
        ranked AS (
            SELECT chain, day, supply_b,
                   supply_b - LAG(supply_b, 7) OVER (
                       PARTITION BY chain ORDER BY day
                   ) AS delta_7d_b,
                   supply_b - LAG(supply_b, 30) OVER (
                       PARTITION BY chain ORDER BY day
                   ) AS delta_30d_b,
                   ROW_NUMBER() OVER (
                       PARTITION BY chain ORDER BY day DESC
                   ) AS rn
            FROM daily
        )
        SELECT chain, day, supply_b, delta_7d_b, delta_30d_b
        FROM ranked
        WHERE day = (SELECT day FROM latest_day) AND supply_b >= %s
        ORDER BY supply_b DESC
        LIMIT %s
    """
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, [min_supply_b, limit])
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for chain, day, supply_b, delta_7d, delta_30d in rows:
        out.append(
            {
                "chain": chain,
                "day": day.isoformat() if isinstance(day, date) else None,
                "supply_usd_b": _to_float(supply_b),
                "delta_7d_usd_b": _to_float(delta_7d),
                "delta_30d_usd_b": _to_float(delta_30d),
            }
        )
    return out


def _query_by_stablecoin(
    chain: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Per-stablecoin breakdown on the latest day for one chain."""
    # B-109 retired the DISTINCT ON workaround. PK on (asset_id, chain, ts)
    # + day-aligned ts means each (chain, asset_id, day) has at most one
    # row natively.
    sql = """
        WITH latest_day AS (
            SELECT MAX(ts::date) AS day
            FROM defillama.stablecoins
            WHERE chain = %s
        ),
        latest_rows AS (
            SELECT asset_id, symbol, name, peg_type, supply_usd
            FROM defillama.stablecoins
            WHERE chain = %s
              AND ts::date = (SELECT day FROM latest_day)
        )
        SELECT (SELECT day FROM latest_day) AS day, asset_id, symbol, name,
               peg_type, supply_usd / 1e9 AS supply_b
        FROM latest_rows
        ORDER BY supply_b DESC
        LIMIT %s
    """
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, [chain, chain, limit])
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for day, asset_id, symbol, name, peg_type, supply_b in rows:
        out.append(
            {
                "chain": chain,
                "day": day.isoformat() if isinstance(day, date) else None,
                "asset_id": asset_id,
                "symbol": symbol,
                "name": name,
                "peg_type": peg_type,
                "supply_usd_b": _to_float(supply_b),
            }
        )
    return out


def _query_chains_with_data() -> list[dict[str, Any]]:
    """Enumerate every chain with recent stablecoin presence (for --list-chains)."""
    # B-109 retired the DISTINCT ON workaround. PK enforces uniqueness.
    sql = """
        WITH latest_day AS (
            SELECT MAX(ts::date) AS day
            FROM defillama.stablecoins
        )
        SELECT chain,
               SUM(supply_usd) / 1e9 AS supply_b,
               COUNT(DISTINCT asset_id) AS n_assets,
               MAX(ts::date) AS latest_day
        FROM defillama.stablecoins
        WHERE ts::date = (SELECT day FROM latest_day)
        GROUP BY chain
        ORDER BY supply_b DESC
    """
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for chain, supply_b, n_assets, latest_day in rows:
        out.append(
            {
                "chain": chain,
                "supply_usd_b": _to_float(supply_b),
                "n_assets": int(n_assets) if n_assets is not None else 0,
                "latest_day": latest_day.isoformat() if isinstance(latest_day, date) else None,
            }
        )
    return out


def _fmt_b(value: Optional[float], width: int, *, sign: bool = False) -> str:
    """Format a USD-billions value into a right-aligned cell."""
    if value is None:
        return f"{'-':>{width}}"
    fmt = f"{{:>+{width},.2f}}" if sign else f"{{:>{width},.2f}}"
    return fmt.format(value)


def _format_trajectory_human(
    chain: str, rows: list[dict[str, Any]], horizon_tag: str
) -> str:
    if not rows:
        return (
            f"No defillama.stablecoins rows for chain {chain!r}. "
            "Check `genkei stablecoin-flow --list-chains` for valid chain names."
        )
    header = (
        f"{chain} stablecoin supply | trajectory | "
        f"horizon={horizon_tag} | {len(rows)} day{'s' if len(rows) != 1 else ''}"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'day':<12} {'supply_$B':>11} {'Δ_7d_$B':>10} {'Δ_30d_$B':>10}"
    )
    for row in rows:
        day = row["day"] or "-"
        supply = _fmt_b(row["supply_usd_b"], 11)
        delta7 = _fmt_b(row["delta_7d_usd_b"], 10, sign=True)
        delta30 = _fmt_b(row["delta_30d_usd_b"], 10, sign=True)
        lines.append(f"  {day:<12} {supply} {delta7} {delta30}")
    lines.append("")
    lines.append(
        "  Δ = current supply − supply N days prior. Positive = stables "
        "minted on this chain (capital arriving); negative = burned / "
        "bridged-out (capital leaving)."
    )
    return "\n".join(lines)


def _format_all_chains_human(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "No defillama.stablecoins rows above the min-supply threshold. "
            "Lower --min-supply-b or check `genkei watchlist health` for "
            "defillama ingest status."
        )
    header = (
        f"Stablecoin supply by chain | all-chains snapshot | "
        f"{len(rows)} chain{'s' if len(rows) != 1 else ''}"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'chain':<16} {'day':<12} {'supply_$B':>11} {'Δ_7d_$B':>10} {'Δ_30d_$B':>10}"
    )
    for row in rows:
        chain = row["chain"][:16]
        day = row["day"] or "-"
        supply = _fmt_b(row["supply_usd_b"], 11)
        delta7 = _fmt_b(row["delta_7d_usd_b"], 10, sign=True)
        delta30 = _fmt_b(row["delta_30d_usd_b"], 10, sign=True)
        lines.append(f"  {chain:<16} {day:<12} {supply} {delta7} {delta30}")
    lines.append("")
    lines.append(
        "  Sorted by current supply desc. Δ_7d / Δ_30d are absolute USD-B "
        "changes vs N days prior. Cross-chain rotation visible as opposite "
        "signs (e.g. Ethereum −Δ_7d while Solana +Δ_7d → capital rotating)."
    )
    return "\n".join(lines)


def _format_by_stablecoin_human(
    chain: str, rows: list[dict[str, Any]]
) -> str:
    if not rows:
        return f"No defillama.stablecoins rows for chain {chain!r} on the latest day."
    day = rows[0]["day"] or "-"
    header = (
        f"{chain} stablecoin supply | by-asset on {day} | "
        f"{len(rows)} asset{'s' if len(rows) != 1 else ''}"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'symbol':<10} {'name':<30} {'peg':<8} {'supply_$B':>11}"
    )
    for row in rows:
        symbol = (row["symbol"] or "-")[:10]
        name = (row["name"] or "-")[:30]
        peg = (row["peg_type"] or "-")[:8]
        supply = _fmt_b(row["supply_usd_b"], 11)
        lines.append(f"  {symbol:<10} {name:<30} {peg:<8} {supply}")
    return "\n".join(lines)


def _format_chain_list_human(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No defillama.stablecoins data found."
    lines = [
        "Chains with stablecoin supply (latest snapshot):",
        "-" * 47,
        f"  {'chain':<20} {'supply_$B':>11} {'n_assets':>9} {'latest_day':<12}",
    ]
    for row in rows:
        chain = row["chain"][:20]
        supply = _fmt_b(row["supply_usd_b"], 11)
        n = row["n_assets"]
        day = row["latest_day"] or "-"
        lines.append(f"  {chain:<20} {supply} {n:>9} {day:<12}")
    return "\n".join(lines)


def stablecoin_flow_cmd(
    chain: Annotated[
        Optional[str],
        typer.Option(
            "--chain",
            "-c",
            help="Chain name (Ethereum, Solana, …) or alias (eth, sol, tron).",
        ),
    ] = None,
    all_chains: Annotated[
        bool,
        typer.Option(
            "--all-chains",
            help="Comparative snapshot across all chains with material supply.",
        ),
    ] = False,
    by_stablecoin: Annotated[
        bool,
        typer.Option(
            "--by-stablecoin",
            help="Per-asset (USDT / USDC / DAI / …) split for the latest day of --chain.",
        ),
    ] = False,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest day (YYYY-MM-DD) for trajectory mode."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest day (YYYY-MM-DD) for trajectory mode."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.", min=1)] = 60,
    min_supply_b: Annotated[
        float,
        typer.Option(
            "--min-supply-b",
            help="Minimum chain supply (USD billions) for --all-chains; filter the long tail.",
            min=0.0,
        ),
    ] = DEFAULT_MIN_SUPPLY_B,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of human table."),
    ] = False,
    list_chains: Annotated[
        bool,
        typer.Option("--list-chains", help="List every chain with stablecoin data and exit."),
    ] = False,
) -> None:
    """Cross-chain stablecoin supply trajectory + rotation signal."""
    _validate_mode_flags(
        list_chains=list_chains,
        all_chains=all_chains,
        chain=chain,
        by_stablecoin=by_stablecoin,
        since=since,
        until=until,
    )

    if list_chains:
        rows = _query_chains_with_data()
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(_format_chain_list_human(rows))
        return

    if all_chains:
        rows = _query_all_chains_snapshot(min_supply_b=min_supply_b, limit=limit)
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(_format_all_chains_human(rows))
        return

    if chain is None:
        raise typer.BadParameter(
            "Either --chain <name>, --all-chains, or --list-chains is required."
        )

    canonical = _resolve_chain(chain)
    horizon_tag = _horizon_tag(canonical)

    if by_stablecoin:
        rows = _query_by_stablecoin(canonical, limit=limit)
        rows = _tag_rows(rows, horizon_tag)
        if json_out:
            typer.echo(json.dumps(rows, indent=2, default=_json_default))
        else:
            typer.echo(_format_by_stablecoin_human(canonical, rows))
        return

    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    if since_d is None and until_d is None:
        # Default lookback: most recent 30 days.
        since_d = _default_since_date()

    rows = _query_chain_trajectory(canonical, since=since_d, until=until_d, limit=limit)
    rows = _tag_rows(rows, horizon_tag)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    else:
        typer.echo(_format_trajectory_human(canonical, rows, horizon_tag))
