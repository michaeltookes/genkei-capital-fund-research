"""``genkei revenue-divergence`` — protocol revenue vs token price (B-062).

Thin CLI wrapper over ``genkei.experiments.protocol_revenue``. The
joining + divergence logic lives in the experiments module so it can
be unit-tested on synthetic series; this file handles flag parsing,
human / JSON output, and watchlist resolution.

Default scope: every watchlist protocol that has a ``coingecko_id``
mapping. Today only the two ``chainlink-*`` slugs map to a token we
also ingest (LINK); the other protocol-watchlist entries pre-declare
their token mappings but stay quiet here until the corresponding
CoinGecko coins land. Surfaces both as one row each with a clear
"missing data" tag so the gap is loud, mirroring ``watchlist health``.

Usage:
  genkei revenue-divergence                                    all mapped protocols
  genkei revenue-divergence --slug chainlink-requests          one protocol detail
  genkei revenue-divergence --slug chainlink-requests --since 2025-01-01
                                                                emit snapshot series
  genkei revenue-divergence --window-days 14 --lookback-days 60
  genkei revenue-divergence --json
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    ProtocolEntry,
    Watchlist,
    load_watchlist,
)
from genkei.experiments.protocol_revenue import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_SIGNIFICANCE_PCT,
    DEFAULT_WINDOW_DAYS,
    DivergenceReport,
    Snapshot,
    build_snapshots,
    diagnose_divergence,
    load_fee_series,
    load_price_series,
)


def _resolve_protocol_or_exit(slug: str, watchlist: Watchlist) -> ProtocolEntry:
    protocol = watchlist.find_protocol(slug)
    if protocol is None:
        raise typer.BadParameter(
            f"Protocol slug {slug!r} not in the watchlist. "
            "Add it under `protocols:` in watchlists.yml first."
        )
    return protocol


def _mapped_protocols(watchlist: Watchlist) -> list[ProtocolEntry]:
    return [p for p in watchlist.protocols if p.coingecko_id]


def _compute_one(
    protocol: ProtocolEntry,
    *,
    since: Optional[date],
    until: Optional[date],
    window_days: int,
    lookback_days: int,
    significance_pct: Decimal,
) -> tuple[list[Snapshot], DivergenceReport]:
    fee_since = since - timedelta(days=window_days - 1) if since is not None else None
    fees = load_fee_series(protocol.slug, since=fee_since, until=until)
    prices = (
        load_price_series(protocol.coingecko_id, since=since, until=until)
        if protocol.coingecko_id is not None
        else []
    )
    snapshots = build_snapshots(fees, prices, window_days=window_days)
    report = diagnose_divergence(
        snapshots,
        slug=protocol.slug,
        coingecko_id=protocol.coingecko_id or "",
        horizon=_horizon_tag(protocol),
        window_days=window_days,
        lookback_days=lookback_days,
        significance_pct=significance_pct,
    )
    return snapshots, report


def _horizon_tag(protocol: ProtocolEntry) -> str:
    sleeve = "core" if protocol.tier == "primary" else "tactical"
    category = (protocol.category or "uncategorized").lower().replace(" ", "-")
    return f"crypto:{sleeve}:{category}"


def _snapshot_to_dict(snap: Snapshot) -> dict[str, Any]:
    return {
        "ts": snap.ts.isoformat(),
        "price_usd": snap.price_usd,
        "market_cap_usd": snap.market_cap_usd,
        "trailing_fees_usd": snap.trailing_fees_usd,
        "trailing_revenue_usd": snap.trailing_revenue_usd,
        "annualized_fees_usd": snap.annualized_fees_usd,
        "annualized_revenue_usd": snap.annualized_revenue_usd,
        "pf_ratio": snap.pf_ratio,
        "pr_ratio": snap.pr_ratio,
    }


def _report_to_dict(report: DivergenceReport, protocol: ProtocolEntry) -> dict[str, Any]:
    return {
        "slug": report.slug,
        "name": protocol.name,
        "category": protocol.category,
        "coingecko_id": report.coingecko_id or None,
        "horizon_tag": report.horizon,
        "as_of": report.as_of.isoformat(),
        "window_days": report.window_days,
        "lookback_days": report.lookback_days,
        "price_change_pct": report.price_change_pct,
        "revenue_change_pct": report.revenue_change_pct,
        "pf_ratio_now": report.pf_ratio_now,
        "pf_ratio_lookback": report.pf_ratio_lookback,
        "kind": report.kind,
    }


def _format_single_human(
    protocol: ProtocolEntry,
    snapshots: list[Snapshot],
    report: DivergenceReport,
    *,
    show_series: bool,
) -> str:
    header_line = (
        f"{protocol.slug} → {protocol.coingecko_id or '(unmapped)'} "
        f"({protocol.name}, {protocol.category or 'uncategorized'}, horizon={report.horizon})"
    )
    lines = [header_line, "-" * len(header_line)]
    lines.append(
        f"  as_of={report.as_of.isoformat()}  "
        f"window={report.window_days}d  lookback={report.lookback_days}d"
    )
    lines.append(f"  divergence:        {report.kind}")
    lines.append(
        "  price_change:      "
        + _fmt_pct(report.price_change_pct)
    )
    lines.append(
        "  revenue_change:    "
        + _fmt_pct(report.revenue_change_pct)
    )
    lines.append("  P/F now:           " + _fmt_ratio(report.pf_ratio_now))
    lines.append("  P/F lookback:      " + _fmt_ratio(report.pf_ratio_lookback))
    if show_series and snapshots:
        lines.append("")
        lines.append(
            f"  {'ts':<12} {'market_cap':>18} {'ann_fees':>16} "
            f"{'ann_rev':>16} {'P/F':>10} {'P/R':>10}"
        )
        for snap in snapshots:
            lines.append(
                f"  {snap.ts.isoformat():<12} "
                f"{_fmt_money(snap.market_cap_usd, 18)} "
                f"{_fmt_money(snap.annualized_fees_usd, 16)} "
                f"{_fmt_money(snap.annualized_revenue_usd, 16)} "
                f"{_fmt_ratio_col(snap.pf_ratio, 10)} "
                f"{_fmt_ratio_col(snap.pr_ratio, 10)}"
            )
    return "\n".join(lines)


def _format_table_human(rows: list[tuple[ProtocolEntry, DivergenceReport]]) -> str:
    if not rows:
        return (
            "No protocol-token mappings found in the watchlist. "
            "Add `coingecko_id:` to entries under `protocols:` in watchlists.yml."
        )
    header = (
        f"{'slug':<22} {'horizon':<24} {'token':<22} {'kind':<20} "
        f"{'price%':>10} {'rev%':>10} {'P/F now':>12}"
    )
    lines = [header, "-" * len(header)]
    sorted_rows = sorted(
        rows,
        key=lambda pr: (
            -_divergence_score(pr[1]),
            pr[0].slug,
        ),
    )
    for protocol, report in sorted_rows:
        lines.append(
            f"  {protocol.slug:<20} "
            f"{report.horizon:<24} "
            f"{(protocol.coingecko_id or '-'):<22} "
            f"{report.kind:<20} "
            f"{_fmt_pct(report.price_change_pct):>10} "
            f"{_fmt_pct(report.revenue_change_pct):>10} "
            f"{_fmt_ratio_col(report.pf_ratio_now, 12)}"
        )
    return "\n".join(lines)


def _divergence_score(report: DivergenceReport) -> float:
    """Sort key: rank divergences ahead of aligned/insufficient rows."""
    if report.kind in {"price-leads-up", "price-leads-down"}:
        magnitude = 0.0
        if report.price_change_pct is not None:
            magnitude += float(abs(report.price_change_pct))
        if report.revenue_change_pct is not None:
            magnitude += float(abs(report.revenue_change_pct))
        return magnitude
    return -1.0


def _fmt_pct(value: Optional[Decimal]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def _fmt_ratio(value: Optional[Decimal]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}x"


def _fmt_ratio_col(value: Optional[Decimal], width: int) -> str:
    formatted = _fmt_ratio(value)
    return f"{formatted:>{width}}"


def _fmt_money(value: Optional[Decimal], width: int) -> str:
    if value is None:
        return f"{'n/a':>{width}}"
    return f"${float(value):>{width - 1},.0f}"


def revenue_divergence_cmd(
    slug: Annotated[
        Optional[str],
        typer.Option(
            "--slug",
            help="DefiLlama protocol slug. If omitted, summarize every mapped protocol.",
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Start date (YYYY-MM-DD). Emits time series in --slug mode."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="End date (YYYY-MM-DD)."),
    ] = None,
    window_days: Annotated[
        int,
        typer.Option(
            "--window-days",
            help="Trailing window for fees/revenue (days).",
            min=1,
        ),
    ] = DEFAULT_WINDOW_DAYS,
    lookback_days: Annotated[
        int,
        typer.Option(
            "--lookback-days",
            help="Lookback span for trend comparison (days).",
            min=1,
        ),
    ] = DEFAULT_LOOKBACK_DAYS,
    significance_pct: Annotated[
        float,
        typer.Option(
            "--significance-pct",
            help="Trend deltas smaller than this are treated as flat (percent).",
            min=0.0,
        ),
    ] = float(DEFAULT_SIGNIFICANCE_PCT),
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of human table."),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Compare protocol revenue trend to token price trend; flag divergences."""
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    sig_pct = Decimal(str(significance_pct))

    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if slug is not None:
        protocol = _resolve_protocol_or_exit(slug, watchlist)
        if protocol.coingecko_id is None:
            raise typer.BadParameter(
                f"Protocol {slug!r} has no coingecko_id mapping in the watchlist. "
                "Add one under the protocol entry to enable revenue-vs-price analysis."
            )
        snapshots, report = _compute_one(
            protocol,
            since=since_d,
            until=until_d,
            window_days=window_days,
            lookback_days=lookback_days,
            significance_pct=sig_pct,
        )
        if json_out:
            payload = _report_to_dict(report, protocol)
            if since_d is not None:
                payload["snapshots"] = [_snapshot_to_dict(s) for s in snapshots]
            typer.echo(json.dumps(payload, indent=2, default=_json_default))
        else:
            typer.echo(
                _format_single_human(
                    protocol, snapshots, report, show_series=since_d is not None
                )
            )
        return

    protocols = _mapped_protocols(watchlist)
    rows: list[tuple[ProtocolEntry, DivergenceReport]] = []
    for protocol in protocols:
        _, report = _compute_one(
            protocol,
            since=since_d,
            until=until_d,
            window_days=window_days,
            lookback_days=lookback_days,
            significance_pct=sig_pct,
        )
        rows.append((protocol, report))

    if json_out:
        typer.echo(
            json.dumps(
                [_report_to_dict(report, protocol) for protocol, report in rows],
                indent=2,
                default=_json_default,
            )
        )
    else:
        typer.echo(_format_table_human(rows))
