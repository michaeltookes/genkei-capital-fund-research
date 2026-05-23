"""``genkei macro-regime`` — macro regime classifier (B-059).

Thin CLI wrapper over ``genkei.experiments.macro_regime``. The math
lives in the Postgres view ``analytics.macro_regime_per_date`` and
the equivalent Python ``classify`` function; this command shapes the
query (default = latest day; ``--since`` / ``--until`` / ``--limit``
control history; ``--summary`` collapses to a regime-distribution
table) and renders the result.

Usage:
  genkei macro-regime                              latest day, breakdown
  genkei macro-regime --since 2020-01-01           daily history
  genkei macro-regime --since 2008-09-01 --until 2008-12-31
  genkei macro-regime --summary --since 2020-01-01 distribution table
  genkei macro-regime --limit 30                   last 30 days
  genkei macro-regime --json                       machine-readable
"""

import json
from decimal import Decimal
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.experiments.macro_regime import (
    DEFAULT_HORIZON,
    REGIME_LABELS,
    RegimeResult,
    load_regimes,
    summarize,
)


def _fmt_decimal(value: Optional[Decimal], width: int = 7, precision: int = 2) -> str:
    """Right-aligned numeric formatting; ``-`` when value is None."""
    if value is None:
        return "-".rjust(width)
    return f"{float(value):>{width}.{precision}f}"


def _format_breakdown(r: RegimeResult) -> str:
    """One-line human breakdown of the inputs that drove the label."""
    return (
        f"DGS10={_fmt_decimal(r.dgs10, 5)} "
        f"(Δ30d={_fmt_decimal(r.dgs10_30d_change, 6)}), "
        f"HY={_fmt_decimal(r.hy_oas, 5)} "
        f"(Δ30d={_fmt_decimal(r.hy_oas_30d_change, 6)}), "
        f"VIX={_fmt_decimal(r.vix, 5)}, "
        f"USD={_fmt_decimal(r.usd_index, 8, 3)} "
        f"(Δ30d={_fmt_decimal(r.usd_index_30d_change, 7, 3)})"
    )


def _format_human(results: list[RegimeResult]) -> str:
    if not results:
        return f"No regime rows for the given range. [horizon={DEFAULT_HORIZON}]"
    if len(results) == 1:
        r = results[0]
        return (
            f"{r.ts.isoformat()} — {r.regime} "
            f"(inputs={r.available_inputs}/4, horizon={r.horizon})\n"
            f"  {_format_breakdown(r)}"
        )
    lines = [
        f"{len(results)} day{'s' if len(results) != 1 else ''} of regime history "
        f"(horizon={results[0].horizon})",
        "-" * 56,
        f"  {'ts':<12} {'regime':<19} {'inputs':>6}  breakdown",
    ]
    for r in results:
        lines.append(
            f"  {r.ts.isoformat():<12} {r.regime:<19} {r.available_inputs:>3}/4   "
            f"{_format_breakdown(r)}"
        )
    return "\n".join(lines)


def _format_summary(results: list[RegimeResult]) -> str:
    if not results:
        return f"No regime rows for the given range. [horizon={DEFAULT_HORIZON}]"
    counts = summarize(results)
    total = len(results)
    lines = [
        f"Regime distribution across {total} day{'s' if total != 1 else ''} "
        f"(horizon={results[0].horizon})",
        "-" * 56,
        f"  {'regime':<20} {'days':>8} {'share':>10}",
    ]
    for label in REGIME_LABELS:
        n = counts[label]
        share = (n / total * 100) if total else 0.0
        lines.append(f"  {label:<20} {n:>8} {share:>9.1f}%")
    lines.append("")
    lines.append(f"Range: {results[-1].ts.isoformat()} → {results[0].ts.isoformat()}")
    return "\n".join(lines)


def _result_to_dict(r: RegimeResult) -> dict[str, Any]:
    return {
        "ts": r.ts.isoformat(),
        "regime": r.regime,
        "horizon_tag": r.horizon,
        "available_inputs": r.available_inputs,
        "dgs10": r.dgs10,
        "dgs10_30d_change": r.dgs10_30d_change,
        "hy_oas": r.hy_oas,
        "hy_oas_30d_change": r.hy_oas_30d_change,
        "vix": r.vix,
        "usd_index": r.usd_index,
        "usd_index_30d_change": r.usd_index_30d_change,
    }


def macro_regime_cmd(
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Start date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="End date (YYYY-MM-DD)."),
    ] = None,
    limit: Annotated[
        Optional[int],
        typer.Option("--limit", help="Max rows. Defaults to 1 if no range."),
    ] = None,
    summary: Annotated[
        bool,
        typer.Option(
            "--summary",
            help="Collapse to regime-distribution counts over the range.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Machine-readable JSON output."),
    ] = False,
) -> None:
    """Macro regime label per date from ``analytics.macro_regime_per_date``.

    Default behavior (no flags) returns the single most recent row
    with a per-component breakdown. ``--since`` / ``--until`` widen
    the range; ``--summary`` collapses to a distribution table.
    """
    parsed_since = _parse_date(since, label="since") if since else None
    parsed_until = _parse_date(until, label="until") if until else None

    # No range → default to last 1 row (= today's regime).
    effective_limit = limit
    if effective_limit is None and parsed_since is None and parsed_until is None:
        effective_limit = 1

    results = load_regimes(
        since=parsed_since,
        until=parsed_until,
        limit=effective_limit,
    )

    if json_output:
        if summary:
            payload: dict[str, Any] = {
                "total_days": len(results),
                "horizon_tag": results[0].horizon if results else DEFAULT_HORIZON,
                "counts": summarize(results),
            }
            if results:
                payload["range"] = {
                    "start": results[-1].ts.isoformat(),
                    "end": results[0].ts.isoformat(),
                }
        else:
            payload = {
                "results": [_result_to_dict(r) for r in results],
                "count": len(results),
            }
        typer.echo(json.dumps(payload, default=_json_default, indent=2))
        return

    if summary:
        typer.echo(_format_summary(results))
    else:
        typer.echo(_format_human(results))
