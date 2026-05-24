"""``genkei eight-k-impact`` — 8-K filing event study (B-057).

Thin CLI wrapper over ``genkei.experiments.eight_k_impact``. Runs the
event-study aggregator over all 8-K filings for watchlist equities
(or one ticker via ``--ticker``) and reports overall + per-stratum
return windows.

Usage:
  genkei eight-k-impact                              # overall + all stratifications
  genkei eight-k-impact --ticker AAPL                # one issuer
  genkei eight-k-impact --by item-code               # only item-code stratification
  genkei eight-k-impact --since 2010-01-01           # filter event date range
  genkei eight-k-impact --json                       # machine-readable
"""

from __future__ import annotations

import json
from datetime import date as date_type
from decimal import Decimal
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.experiments.eight_k_impact import (
    DEFAULT_HORIZON,
    DEFAULT_WINDOWS,
    StratumStats,
    aggregate,
    run_event_study,
    stratify_by_item_code,
    stratify_by_regime,
    stratify_by_ticker,
)


def _fmt_pct(value: Decimal | None, width: int = 8) -> str:
    if value is None:
        return "n/a".rjust(width)
    return f"{float(value):>{width-1}.3f}%"


def _format_stratum_row(s: StratumStats, key_width: int = 20) -> str:
    parts = [f"  {s.stratum_key:<{key_width}} {s.n_events:>5}"]
    for label, _, _ in DEFAULT_WINDOWS:
        parts.append(_fmt_pct(s.mean_pct.get(label), 10))
    return "  ".join(parts)


def _format_stratum_block(title: str, strata: list[StratumStats]) -> str:
    if not strata:
        return f"\n{title}\n  (no rows)"
    lines = [f"\n{title}"]
    header_parts = [f"  {'stratum':<20} {'n':>5}"]
    for label, _, _ in DEFAULT_WINDOWS:
        header_parts.append(f"{label:>9}_mean")
    lines.append("  ".join(header_parts))
    for s in strata:
        lines.append(_format_stratum_row(s))
    return "\n".join(lines)


def _stratum_to_dict(s: StratumStats) -> dict[str, Any]:
    return {
        "stratum_key": s.stratum_key,
        "n_events": s.n_events,
        "horizon_tag": s.horizon,
        "mean_pct": s.mean_pct,
        "median_pct": s.median_pct,
        "hit_rate_pct": s.hit_rate_pct,
    }


def eight_k_impact_cmd(
    ticker: Annotated[
        Optional[str],
        typer.Option(
            "--ticker",
            help="Restrict to one issuer's filings.",
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest filing date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest filing date (YYYY-MM-DD)."),
    ] = None,
    by: Annotated[
        str,
        typer.Option(
            "--by",
            help=(
                "Which stratifications to include. Comma-separated subset of "
                "{ticker,item-code,regime}. Default: all three."
            ),
        ),
    ] = "ticker,item-code,regime",
    top: Annotated[
        int,
        typer.Option(
            "--top",
            min=1,
            help="Show only the top-N strata by event count.",
        ),
    ] = 10,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Machine-readable JSON output."),
    ] = False,
) -> None:
    """Run the 8-K filing impact event study (B-057)."""
    parsed_since: date_type | None = (
        _parse_date(since, label="--since") if since else None
    )
    parsed_until: date_type | None = (
        _parse_date(until, label="--until") if until else None
    )
    if (
        parsed_since is not None
        and parsed_until is not None
        and parsed_since > parsed_until
    ):
        raise typer.BadParameter("--since must be on or before --until.")

    requested_strata = {s.strip() for s in by.split(",") if s.strip()}
    unknown = requested_strata - {"ticker", "item-code", "regime"}
    if unknown:
        raise typer.BadParameter(
            f"Unknown stratification(s): {sorted(unknown)}. "
            "Valid: ticker, item-code, regime."
        )

    event_returns = run_event_study(
        ticker=ticker, since=parsed_since, until=parsed_until
    )
    overall = aggregate(event_returns)

    by_ticker = (
        stratify_by_ticker(event_returns) if "ticker" in requested_strata else []
    )
    by_item = (
        stratify_by_item_code(event_returns)
        if "item-code" in requested_strata
        else []
    )
    by_regime_strata = (
        stratify_by_regime(event_returns) if "regime" in requested_strata else []
    )

    # Top-N each by event count.
    by_ticker = sorted(by_ticker, key=lambda s: -s.n_events)[:top]
    by_item = sorted(by_item, key=lambda s: -s.n_events)[:top]
    by_regime_strata = sorted(by_regime_strata, key=lambda s: -s.n_events)[:top]

    if json_output:
        payload: dict[str, Any] = {
            "n_events": overall.n_events,
            "horizon_tag": overall.horizon,
            "overall": _stratum_to_dict(overall),
            "by_ticker": [_stratum_to_dict(s) for s in by_ticker],
            "by_item_code": [_stratum_to_dict(s) for s in by_item],
            "by_regime": [_stratum_to_dict(s) for s in by_regime_strata],
        }
        typer.echo(json.dumps(payload, default=_json_default, indent=2))
        return

    typer.echo(
        f"8-K filing impact event study (B-057) — {overall.n_events:,} events "
        f"[horizon={overall.horizon or DEFAULT_HORIZON}]"
    )
    typer.echo("=" * 80)
    typer.echo(
        f"\nOverall (n={overall.n_events:,})\n"
        + _format_stratum_row(
            StratumStats(
                stratum_key="ALL",
                n_events=overall.n_events,
                horizon=overall.horizon,
                mean_pct=overall.mean_pct,
                median_pct=overall.median_pct,
                hit_rate_pct=overall.hit_rate_pct,
            )
        )
    )

    if by_ticker:
        typer.echo(_format_stratum_block("By ticker", by_ticker))
    if by_item:
        typer.echo(_format_stratum_block("By 8-K item code", by_item))
    if by_regime_strata:
        typer.echo(_format_stratum_block("By macro regime", by_regime_strata))
