"""``genkei signals`` — query the cross-source correlation engine (B-064).

Two roles in one subcommand:

* **Default** — run the correlator over recent ``meta.signal_events``
  with the configured rules and surface every stack that qualifies.
  Sorted by ``window_end DESC, score DESC`` so the freshest, strongest
  signals are first.

* **``--events``** — bypass the correlator and dump raw signal events
  matching the filter. Useful for sanity-checking that emitters are
  actually writing data, debugging a missing stack, or seeing the
  payload for one event.

Filters share semantics with the rest of the CLI (``--asset`` /
``--since`` / ``--until`` / ``--top`` / ``--json``). ``--rule`` scopes
the correlator to a single rule; ``--direction`` filters by bullish /
bearish.
"""

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.experiments.signal_benchmark import (
    DEFAULT_CRYPTO_BENCHMARK,
    DEFAULT_EQUITY_BENCHMARK,
    StackBenchmarkContext,
    compute_stack_benchmark_contexts,
)
from genkei.experiments.signal_rules import DEFAULT_RULES_PATH, load_rules
from genkei.experiments.signal_store import (
    ASSET_CLASSES,
    Stack,
    detect_stacks,
    query_events,
)


def _date_to_dt(d: Optional[date], *, end_of_day: bool = False) -> Optional[datetime]:
    if d is None:
        return None
    day_time = (
        time(23, 59, 59, 999999, tzinfo=timezone.utc)
        if end_of_day
        else time(0, 0, tzinfo=timezone.utc)
    )
    return datetime.combine(d, day_time)


def _stack_to_dict(
    stack: Stack,
    *,
    benchmark: Optional[StackBenchmarkContext] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "rule": stack.rule_name,
        "asset": stack.asset,
        "asset_class": stack.asset_class,
        "horizon_tag": stack.horizon,
        "direction": stack.direction,
        "window_start": stack.window_start.isoformat(),
        "window_end": stack.window_end.isoformat(),
        "span_days": (stack.window_end - stack.window_start).days,
        "score": stack.score,
        "distinct_sources": stack.distinct_sources,
        "event_count": stack.event_count,
        "events": [
            {
                "source": ev.source,
                "signal_kind": ev.signal_kind,
                "ts": ev.ts.isoformat(),
                "direction": ev.direction,
                "strength": ev.strength,
                "source_ref": ev.source_ref,
            }
            for ev in stack.events
        ],
    }
    if benchmark is not None:
        out["benchmark"] = {
            "ticker": benchmark.benchmark_ticker,
            "asset_return_pct": benchmark.asset_return_pct,
            "benchmark_return_pct": benchmark.benchmark_return_pct,
            "abnormal_pct": benchmark.abnormal_pct,
        }
    return out


def _format_pct(value: Optional[Any], width: int) -> str:
    if value is None:
        return f"{'n/a':>{width}}"
    return f"{float(value):>{width}.2f}"


def _format_stacks_human(
    stacks: list[Stack],
    *,
    benchmarks: Optional[list[StackBenchmarkContext]] = None,
) -> str:
    if not stacks:
        return (
            "No stacks found. Either no rules qualified (try widening --since), "
            "no emitters have populated meta.signal_events yet (run "
            "`python -m genkei.experiments.emitters.insider_clusters_emitter --since X`), "
            "or --rule scoped out every match."
        )
    show_benchmark = benchmarks is not None
    header = f"Cross-source signal stacks ({len(stacks)} found)"
    lines = [header, "-" * len(header)]
    if show_benchmark:
        lines.append(
            f"  {'window_end':<12} {'asset':<8} {'dir':<8} {'rule':<24} "
            f"{'horizon':<18} {'score':>6} {'sources':>7} "
            f"{'vs_bench':>9}  events"
        )
    else:
        lines.append(
            f"  {'window_end':<12} {'asset':<8} {'dir':<8} {'rule':<24} "
            f"{'horizon':<18} {'score':>6} {'sources':>7}  events"
        )
    for idx, s in enumerate(stacks):
        date_str = s.window_end.date().isoformat()
        score = f"{float(s.score):>6.2f}"
        events = ", ".join(
            f"{ev.source}/{ev.signal_kind}" for ev in s.events[:4]
        )
        if len(s.events) > 4:
            events += f", +{len(s.events) - 4} more"
        if show_benchmark:
            ctx = benchmarks[idx] if benchmarks else None
            vs_bench = _format_pct(ctx.abnormal_pct if ctx else None, width=9)
            lines.append(
                f"  {date_str:<12} {s.asset:<8} {s.direction:<8} {s.rule_name:<24} "
                f"{s.horizon:<18} {score} {s.distinct_sources:>7} "
                f"{vs_bench}  {events}"
            )
        else:
            lines.append(
                f"  {date_str:<12} {s.asset:<8} {s.direction:<8} {s.rule_name:<24} "
                f"{s.horizon:<18} {score} {s.distinct_sources:>7}  {events}"
            )
    if show_benchmark:
        lines.append("")
        lines.append(
            "  vs_bench  = asset_return_pct - benchmark_return_pct over the stack window (pp)"
        )
        lines.append(
            "              positive = asset out-performed its benchmark IN window"
        )
        lines.append(
            "              (bullish rule: confirms the call; bearish: market-relative miss)"
        )
    return "\n".join(lines)


def _format_events_human(events: list[Any]) -> str:
    if not events:
        return (
            "No signal events. Run an emitter (e.g. "
            "`python -m genkei.experiments.emitters.insider_clusters_emitter --since 2024-01-01`) "
            "to populate meta.signal_events."
        )
    header = f"Signal events ({len(events)} found)"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'ts':<12} {'asset':<8} {'source':<22} {'signal_kind':<18} "
        f"{'horizon':<18} {'dir':<8} {'strength':>8}  source_ref"
    )
    for ev in events:
        d = ev.ts.date().isoformat()
        strength = (
            f"{float(ev.strength):>8.3f}" if ev.strength is not None else "    n/a"
        )
        ref = ev.source_ref or "-"
        lines.append(
            f"  {d:<12} {ev.asset:<8} {ev.source:<22} {ev.signal_kind:<18} "
            f"{ev.horizon:<18} {ev.direction:<8} {strength}  {ref}"
        )
    return "\n".join(lines)


def signals_cmd(
    asset: Annotated[
        Optional[str],
        typer.Option(
            "--asset",
            "-a",
            help="Limit to one asset (equity ticker or coingecko id).",
        ),
    ] = None,
    rule: Annotated[
        Optional[str],
        typer.Option(
            "--rule",
            help="Run only the named correlation rule.",
        ),
    ] = None,
    direction: Annotated[
        Optional[str],
        typer.Option(
            "--direction",
            help="Filter to bullish / bearish / neutral.",
        ),
    ] = None,
    asset_class: Annotated[
        Optional[str],
        typer.Option(
            "--asset-class",
            help=(
                "Limit to one asset class (crypto / equity / protocol / macro). "
                "Crypto stacks are sparse next to the equity flow, so "
                "`--asset-class crypto` is the reliable way to focus on them "
                "regardless of how many equity stacks are live (B-130)."
            ),
        ),
    ] = None,
    horizon: Annotated[
        Optional[str],
        typer.Option(
            "--horizon",
            help=(
                "Limit to one exact horizon tag, e.g. `crypto:tactical` or "
                "`equity:core` — finer than --asset-class."
            ),
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest event date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest event date (YYYY-MM-DD)."),
    ] = None,
    events: Annotated[
        bool,
        typer.Option(
            "--events",
            help="Dump raw signal events instead of running the correlator.",
        ),
    ] = False,
    top: Annotated[
        int,
        typer.Option("--top", help="Max rows.", min=1),
    ] = 30,
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
    benchmark: Annotated[
        bool,
        typer.Option(
            "--benchmark/--no-benchmark",
            help=(
                "Show benchmark-adjusted column (B-100). Equity stacks compare "
                "vs SPY from yahoo.candles, crypto stacks vs BTC from "
                "coinbase.candles. Default on; --no-benchmark disables."
            ),
        ),
    ] = True,
    equity_benchmark: Annotated[
        str,
        typer.Option(
            "--equity-benchmark",
            help="Equity-side benchmark ticker (from yahoo.candles).",
        ),
    ] = DEFAULT_EQUITY_BENCHMARK,
    crypto_benchmark: Annotated[
        str,
        typer.Option(
            "--crypto-benchmark",
            help="Crypto-side benchmark symbol (from coinbase.candles as <SYMBOL>-USD).",
        ),
    ] = DEFAULT_CRYPTO_BENCHMARK,
) -> None:
    """Show cross-source signal stacks from the correlation engine."""
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    if direction is not None and direction not in {"bullish", "bearish", "neutral"}:
        raise typer.BadParameter(
            "--direction must be one of bullish / bearish / neutral."
        )
    if asset_class is not None and asset_class not in ASSET_CLASSES:
        raise typer.BadParameter(
            f"--asset-class must be one of {', '.join(sorted(ASSET_CLASSES))}."
        )

    if events:
        # When a class/horizon filter is active, pull unbounded and post-filter
        # so `--top` still yields up to N of the *filtered* events (a SQL LIMIT
        # would truncate to the most-recent N overall before the filter).
        post_filter = asset_class is not None or horizon is not None
        rows = query_events(
            asset=asset,
            direction=direction,
            since=_date_to_dt(since_d),
            until=_date_to_dt(until_d, end_of_day=True),
            limit=None if post_filter else top,
        )
        if asset_class is not None:
            rows = [ev for ev in rows if ev.asset_class == asset_class]
        if horizon is not None:
            rows = [ev for ev in rows if ev.horizon == horizon]
        if post_filter:
            rows = rows[:top]
        if json_out:
            typer.echo(
                json.dumps(
                    [
                        {
                            "ts": ev.ts.isoformat(),
                            "asset": ev.asset,
                            "asset_class": ev.asset_class,
                            "horizon_tag": ev.horizon,
                            "source": ev.source,
                            "signal_kind": ev.signal_kind,
                            "direction": ev.direction,
                            "strength": ev.strength,
                            "payload": ev.payload,
                            "source_ref": ev.source_ref,
                        }
                        for ev in rows
                    ],
                    indent=2,
                    default=_json_default,
                )
            )
        else:
            typer.echo(_format_events_human(rows))
        return

    try:
        rules = load_rules(rules_path)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    if rule is not None:
        rules = [r for r in rules if r.name == rule]
        if not rules:
            raise typer.BadParameter(
                f"No rule named {rule!r} in {rules_path}."
            )

    event_rows = query_events(
        asset=asset,
        direction=direction,
        since=_date_to_dt(since_d),
        until=_date_to_dt(until_d, end_of_day=True),
    )
    stacks = detect_stacks(event_rows, rules)
    if asset_class is not None:
        stacks = [s for s in stacks if s.asset_class == asset_class]
    if horizon is not None:
        stacks = [s for s in stacks if s.horizon == horizon]
    # Truncate AFTER the class/horizon filter so `--top` bounds the filtered
    # view (e.g. the 30 most-recent *crypto* stacks), not a global slice that
    # equity would dominate.
    stacks = stacks[:top]

    benchmark_contexts: Optional[list[StackBenchmarkContext]] = None
    if benchmark:
        benchmark_contexts = compute_stack_benchmark_contexts(
            stacks,
            equity_benchmark=equity_benchmark,
            crypto_benchmark=crypto_benchmark,
        )

    if json_out:
        if benchmark_contexts is not None:
            payload = [
                _stack_to_dict(s, benchmark=benchmark_contexts[i])
                for i, s in enumerate(stacks)
            ]
        else:
            payload = [_stack_to_dict(s) for s in stacks]
        typer.echo(
            json.dumps(
                payload,
                indent=2,
                default=_json_default,
            )
        )
    else:
        typer.echo(_format_stacks_human(stacks, benchmarks=benchmark_contexts))
