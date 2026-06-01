"""``genkei backtest`` — stack-outcome backtest CLI (B-101).

Renders the per-stratum forward-return summary from
``stack_backtest.run_backtest`` in two modes:

* **Default** — one row per stratum (rule, direction, or asset) with
  per-window mean / hit-rate / excess-vs-baseline.
* **``--json``** — machine-readable JSON, one object per stratum, all
  windows + n_stacks + per-window n_evaluable.

Stratification (``--by``):
  * ``rule`` (default) — per correlation rule, the headline cut.
  * ``direction`` — bullish vs bearish vs neutral aggregate.
  * ``asset`` — per ticker, useful for catching asset-specific patterns
    (e.g. CRM dominates the deterioration_stack count).

Filters share semantics with ``genkei signals``: ``--asset``, ``--rule``,
``--direction``, ``--since``, ``--until``. ``--rules-path`` lets tests
override the YAML location without touching the packaged file.

Reads ``mean_excess_pct`` against the rule direction in the rendered
table — for bullish rules positive excess is the win; for bearish, the
sign flips. The CLI does NOT mask this — the reader sees the raw sign
and interprets it; that's the honest framing for a backtest where the
whole point is to discover what's actually working.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.experiments.signal_rules import DEFAULT_RULES_PATH
from genkei.experiments.signal_store import DIRECTIONS
from genkei.experiments.stack_backtest import (
    STACK_WINDOWS,
    StackStratumStats,
    run_backtest,
    stratify_by_asset,
    stratify_by_direction,
    stratify_by_rule,
)

STRATIFICATIONS = {
    "rule": stratify_by_rule,
    "direction": stratify_by_direction,
    "asset": stratify_by_asset,
}


def _format_pct(value: Optional[Decimal], *, width: int = 7) -> str:
    if value is None:
        return f"{'n/a':>{width}}"
    return f"{float(value):>{width}.2f}"


def _stratum_to_dict(stats: StackStratumStats) -> dict[str, Any]:
    return {
        "stratum": stats.stratum_key,
        "horizons": sorted(stats.horizons),
        "n_stacks": stats.n_stacks,
        "windows": {
            label: {
                "n_evaluable": stats.n_evaluable.get(label, 0),
                "mean_pct": stats.mean_pct.get(label),
                "median_pct": stats.median_pct.get(label),
                "hit_rate_pct": stats.hit_rate_pct.get(label),
                "mean_excess_pct": stats.mean_excess_pct.get(label),
                "mean_abnormal_pct": stats.mean_abnormal_pct.get(label),
                "n_abnormal_evaluable": stats.n_abnormal_evaluable.get(label, 0),
            }
            for label, _, _ in STACK_WINDOWS
        },
    }


def _has_abnormal(stats_list: list[StackStratumStats]) -> bool:
    """True when any stratum recorded benchmark-adjusted abnormal returns."""
    return any(
        any(value is not None for value in stats.mean_abnormal_pct.values())
        for stats in stats_list
    )


def _format_stratum_table(
    stats_list: list[StackStratumStats],
    stratum_label: str,
    *,
    benchmark_ticker: Optional[str] = None,
) -> str:
    if not stats_list:
        return (
            "No stacks matched the filters. Try widening --since, removing --rule, "
            "or running `genkei signals --top 50` to confirm stacks exist at all."
        )
    show_abnormal = _has_abnormal(stats_list)
    header = (
        f"Backtest by {stratum_label} "
        f"({sum(s.n_stacks for s in stats_list)} stacks across {len(stats_list)} strata)"
    )
    if show_abnormal and benchmark_ticker:
        header += f" — benchmark={benchmark_ticker}"
    lines = [header, "-" * len(header)]
    window_labels = [label for label, _, _ in STACK_WINDOWS]
    # One block per stratum, with one row per window. The flat row format
    # keeps things scannable when there are many strata.
    if show_abnormal:
        col = (
            "  {stratum:<22} {horizon:<14} {window:<10} {n_eval:>6} "
            "{mean:>7} {median:>7} {hit:>6} {excess:>7} {abnormal:>9}"
        )
    else:
        col = (
            "  {stratum:<22} {horizon:<14} {window:<10} {n_eval:>6} "
            "{mean:>7} {median:>7} {hit:>6} {excess:>7}"
        )
    header_fields = {
        "stratum": "stratum",
        "horizon": "horizon",
        "window": "window",
        "n_eval": "n_eval",
        "mean": "mean%",
        "median": "med%",
        "hit": "hit%",
        "excess": "excess",
    }
    if show_abnormal:
        header_fields["abnormal"] = "abnormal"
    lines.append(col.format(**header_fields))
    for stats in stats_list:
        horizon = ",".join(sorted(stats.horizons)) or "n/a"
        for label in window_labels:
            row_fields = {
                "stratum": f"{stats.stratum_key} (n={stats.n_stacks})",
                "horizon": horizon,
                "window": label,
                "n_eval": stats.n_evaluable.get(label, 0),
                "mean": _format_pct(stats.mean_pct.get(label)),
                "median": _format_pct(stats.median_pct.get(label)),
                "hit": _format_pct(stats.hit_rate_pct.get(label), width=6),
                "excess": _format_pct(stats.mean_excess_pct.get(label)),
            }
            if show_abnormal:
                row_fields["abnormal"] = _format_pct(
                    stats.mean_abnormal_pct.get(label), width=9
                )
            lines.append(col.format(**row_fields))
        lines.append("")
    lines.append(
        "  mean%/med%/hit%   = stack-only forward-return distribution per window"
    )
    lines.append(
        "  excess           = stack mean - asset-weighted random-day baseline mean (pp)"
    )
    lines.append(
        "                     positive = stacks beat baseline upward "
        "(bullish rule: good; bearish rule: bad)"
    )
    if show_abnormal:
        bench_label = benchmark_ticker or "benchmark"
        lines.append(
            f"  abnormal         = mean(stack_return - {bench_label}_return) over same window (pp)"
        )
        lines.append(
            "                     positive = stack beat the benchmark in-window "
            "(bullish: good; bearish: bad)"
        )
    return "\n".join(lines)


def backtest_cmd(
    by: Annotated[
        str,
        typer.Option(
            "--by",
            help="Stratify results by rule / direction / asset.",
        ),
    ] = "rule",
    asset: Annotated[
        Optional[str],
        typer.Option("--asset", "-a", help="Limit to one asset (equity ticker)."),
    ] = None,
    rule: Annotated[
        Optional[str],
        typer.Option("--rule", help="Limit to one correlation rule."),
    ] = None,
    direction: Annotated[
        Optional[str],
        typer.Option("--direction", help="Filter to bullish / bearish / neutral."),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest stack window_end date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest stack window_end date (YYYY-MM-DD)."),
    ] = None,
    benchmark: Annotated[
        Optional[str],
        typer.Option(
            "--benchmark",
            help=(
                "Benchmark ticker for abnormal-return column "
                "(e.g. SPY). Must be in watchlists.yml::benchmarks "
                "and have rows in yahoo.candles."
            ),
        ),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    rules_path: Annotated[
        Path,
        typer.Option(
            "--rules-path",
            help="Override the signal-rules YAML location.",
            show_default=True,
        ),
    ] = DEFAULT_RULES_PATH,
) -> None:
    """Backtest historical signal stacks against forward returns (B-101)."""
    if by not in STRATIFICATIONS:
        raise typer.BadParameter(
            f"--by must be one of {sorted(STRATIFICATIONS)}, got {by!r}."
        )
    if direction is not None and direction not in DIRECTIONS:
        raise typer.BadParameter(
            f"--direction must be one of {sorted(DIRECTIONS)}."
        )
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    benchmark_ticker = benchmark.upper() if benchmark else None
    try:
        stack_returns, baselines = run_backtest(
            rule=rule,
            direction=direction,
            asset=asset,
            since=since_d,
            until=until_d,
            benchmark_ticker=benchmark_ticker,
            rules_path=rules_path,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    stratifier = STRATIFICATIONS[by]
    stats_list = stratifier(stack_returns, baselines)

    if json_out:
        typer.echo(
            json.dumps(
                [_stratum_to_dict(s) for s in stats_list],
                indent=2,
                default=_json_default,
            )
        )
    else:
        typer.echo(
            _format_stratum_table(
                stats_list,
                stratum_label=by,
                benchmark_ticker=benchmark_ticker,
            )
        )


__all__ = ["backtest_cmd"]
