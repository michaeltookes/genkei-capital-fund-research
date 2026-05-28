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
from genkei.experiments.signal_rules import DEFAULT_RULES_PATH, load_rules
from genkei.experiments.signal_store import (
    Stack,
    detect_stacks,
    query_events,
)


def _date_to_dt(d: Optional[date]) -> Optional[datetime]:
    if d is None:
        return None
    return datetime.combine(d, time(0, 0, tzinfo=timezone.utc))


def _stack_to_dict(stack: Stack) -> dict[str, Any]:
    return {
        "rule": stack.rule_name,
        "asset": stack.asset,
        "asset_class": stack.asset_class,
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


def _format_stacks_human(stacks: list[Stack]) -> str:
    if not stacks:
        return (
            "No stacks found. Either no rules qualified (try widening --since), "
            "no emitters have populated meta.signal_events yet (run "
            "`python -m genkei.experiments.emitters.insider_clusters_emitter --since X`), "
            "or --rule scoped out every match."
        )
    header = f"Cross-source signal stacks ({len(stacks)} found)"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'window_end':<12} {'asset':<8} {'dir':<8} {'rule':<24} "
        f"{'score':>6} {'sources':>7}  events"
    )
    for s in stacks:
        date_str = s.window_end.date().isoformat()
        score = f"{float(s.score):>6.2f}"
        events = ", ".join(
            f"{ev.source}/{ev.signal_kind}" for ev in s.events[:4]
        )
        if len(s.events) > 4:
            events += f", +{len(s.events) - 4} more"
        lines.append(
            f"  {date_str:<12} {s.asset:<8} {s.direction:<8} {s.rule_name:<24} "
            f"{score} {s.distinct_sources:>7}  {events}"
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
        f"{'dir':<8} {'strength':>8}  source_ref"
    )
    for ev in events:
        d = ev.ts.date().isoformat()
        strength = (
            f"{float(ev.strength):>8.3f}" if ev.strength is not None else "    n/a"
        )
        ref = ev.source_ref or "-"
        lines.append(
            f"  {d:<12} {ev.asset:<8} {ev.source:<22} {ev.signal_kind:<18} "
            f"{ev.direction:<8} {strength}  {ref}"
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

    if events:
        rows = query_events(
            asset=asset,
            direction=direction,
            since=_date_to_dt(since_d),
            until=_date_to_dt(until_d),
            limit=top,
        )
        if json_out:
            typer.echo(
                json.dumps(
                    [
                        {
                            "ts": ev.ts.isoformat(),
                            "asset": ev.asset,
                            "asset_class": ev.asset_class,
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
        until=_date_to_dt(until_d),
    )
    stacks = detect_stacks(event_rows, rules)
    stacks = stacks[:top]

    if json_out:
        typer.echo(
            json.dumps(
                [_stack_to_dict(s) for s in stacks],
                indent=2,
                default=_json_default,
            )
        )
    else:
        typer.echo(_format_stacks_human(stacks))
