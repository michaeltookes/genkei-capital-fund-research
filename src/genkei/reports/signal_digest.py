"""Weekly signal digest — the durable artifact for Phase 6 signal stacks (D-024).

Signal stacks live in ``meta.signal_events`` and are queryable via
``genkei signals``, but until B-122 they had no committed, human-readable
artifact — the research decision log was the lake's *only* durable output.
This module renders the trailing-week correlator output into a dated
markdown file under ``reports/signals/``, grouped by horizon tag (the
``<asset_class>:<sleeve>`` convention CLAUDE.md requires), with a
lake-health footer so a reader knows whether the underlying data was fresh
when the digest was cut.

Design: the pure renderer (:func:`render_weekly_digest`) is separated from
all DB access (:func:`build_weekly_digest`) so the formatting is unit-
testable offline with synthetic ``Stack`` / health rows — the same
collect-vs-render split the ingesters use.

Run it::

    python -m genkei.reports.signal_digest            # writes reports/signals/weekly-<today>.md
    python -m genkei.reports.signal_digest --stdout   # print, don't write
    python -m genkei.reports.signal_digest --since 2026-06-01 --until 2026-06-19

Cadence is weekly via a ``/schedule`` routine (D-024); the module itself is
runner-agnostic, so a GH Actions cron works equally well.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT_DIR = Path("reports/signals")
DEFAULT_LOOKBACK_DAYS = 7


def _max_rule_window_days(rules: list[Any]) -> int:
    """Return the widest correlation window needed to detect current stacks."""
    return max((int(getattr(rule, "window_days", 0) or 0) for rule in rules), default=0)


def _filter_stacks_for_digest_window(
    stacks: list[Any], *, since: datetime, until: datetime
) -> list[Any]:
    """Keep stacks whose terminal event lands inside the rendered digest window."""
    return [stack for stack in stacks if since <= stack.window_end <= until]


def _fmt_pct(value: Any | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):+.2f}pp"


def _fmt_score(value: Any) -> str:
    return f"{float(value):.2f}"


def _events_summary(events: list[Any], *, limit: int = 4) -> str:
    rendered = ", ".join(f"{ev.source}/{ev.signal_kind}" for ev in events[:limit])
    if len(events) > limit:
        rendered += f", +{len(events) - limit} more"
    return rendered or "—"


def render_weekly_digest(
    stacks: list[Any],
    *,
    benchmark_contexts: list[Any] | None = None,
    health_rows: list[dict[str, Any]] | None = None,
    since: date,
    until: date,
    generated_at: datetime,
) -> str:
    """Render the weekly digest markdown. Pure — no DB or clock access.

    ``stacks`` is the correlator output (``signal_store.Stack``), already
    sorted newest-first. ``benchmark_contexts`` is the parallel list from
    ``compute_stack_benchmark_contexts`` (or None to omit the vs-bench
    column). ``health_rows`` are ``meta.ingest_runs`` health dicts (each
    with ``source`` / ``endpoint`` / ``age_hours`` / ``health_status``).
    """
    span_days = (until - since).days
    lines: list[str] = []
    lines.append("# Genkei Capital — Weekly Signal Digest")
    lines.append("")
    lines.append(
        f"**Window:** {since.isoformat()} → {until.isoformat()} "
        f"({span_days} days) · **Generated:** "
        f"{generated_at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    lines.append("")
    lines.append(
        "Cross-source signal stacks from the correlation engine "
        "(`genkei signals`), grouped by horizon tag. Empty horizon "
        "sections are omitted; a week with no qualifying stacks is itself "
        "the signal."
    )
    lines.append("")

    # ---- Summary -----------------------------------------------------------
    bullish = sum(1 for s in stacks if s.direction == "bullish")
    bearish = sum(1 for s in stacks if s.direction == "bearish")
    neutral = len(stacks) - bullish - bearish
    horizons = sorted({s.horizon for s in stacks})
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"- **{len(stacks)}** stack(s): {bullish} bullish / {bearish} bearish"
        + (f" / {neutral} neutral" if neutral else "")
    )
    if stacks:
        lines.append(f"- Horizons present: {', '.join(f'`{h}`' for h in horizons)}")
    else:
        lines.append(
            "- No cross-source stacks qualified this week. Either no rule "
            "cleared its `min_distinct_sources`/`min_score`, or emitters "
            "are sparse — check `genkei signals --events` and `genkei "
            "watchlist health`."
        )
    lines.append("")

    # ---- Stacks by horizon -------------------------------------------------
    show_bench = benchmark_contexts is not None
    # index stacks → their benchmark context positionally (parallel lists;
    # avoid zip(strict=) — unavailable on the 3.9 local test venv)
    bench_by_id = {}
    if show_bench:
        for i, s in enumerate(stacks):
            if i < len(benchmark_contexts):
                bench_by_id[id(s)] = benchmark_contexts[i]
    for horizon in horizons:
        group = [s for s in stacks if s.horizon == horizon]
        lines.append(f"## `{horizon}` — {len(group)} stack(s)")
        lines.append("")
        head = "| window_end | asset | dir | rule | score | sources |"
        sep = "|---|---|---|---|---|---|"
        if show_bench:
            head += " vs_bench |"
            sep += "---|"
        head += " events |"
        sep += "---|"
        lines.append(head)
        lines.append(sep)
        for s in group:
            row = (
                f"| {s.window_end.date().isoformat()} | {s.asset} | "
                f"{s.direction} | {s.rule_name} | {_fmt_score(s.score)} | "
                f"{s.distinct_sources} |"
            )
            if show_bench:
                ctx = bench_by_id.get(id(s))
                row += f" {_fmt_pct(ctx.abnormal_pct if ctx else None)} |"
            row += f" {_events_summary(s.events)} |"
            lines.append(row)
        lines.append("")

    # ---- Lake health -------------------------------------------------------
    lines.append("## Lake health (at generation)")
    lines.append("")
    if not health_rows:
        lines.append("_Health snapshot unavailable._")
    else:
        not_ok = [r for r in health_rows if r.get("health_status") not in (None, "OK")]
        lines.append(
            f"{len(health_rows)} source(s) tracked · "
            f"{len(health_rows) - len(not_ok)} OK · {len(not_ok)} needs attention."
        )
        if not_ok:
            lines.append("")
            lines.append("| source | endpoint | age (h) | status |")
            lines.append("|---|---|---|---|")
            for r in not_ok:
                age = r.get("age_hours")
                age_s = f"{float(age):.1f}" if age is not None else "n/a"
                lines.append(
                    f"| {r.get('source','?')} | {r.get('endpoint','?')} | "
                    f"{age_s} | {r.get('health_status','?')} |"
                )
    lines.append("")

    # ---- Footer ------------------------------------------------------------
    lines.append("---")
    lines.append("")
    legend = (
        "_Horizon tag is `<asset_class>:<sleeve>` — which sleeve the signal "
        "informs (CLAUDE.md). "
    )
    if show_bench:
        legend += (
            "`vs_bench` is the stack asset's return minus its benchmark "
            "(SPY for equity, BTC for crypto) over the stack window, in "
            "percentage points. "
        )
    legend += "Generated by `python -m genkei.reports.signal_digest` (D-024)._"
    lines.append(legend)
    return "\n".join(lines) + "\n"


def build_weekly_digest(
    *,
    since: date | None = None,
    until: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    rules_path: Path | None = None,
    benchmark: bool = True,
) -> tuple[str, date]:
    """Fetch correlator + health data from Postgres and render the digest.

    Returns ``(markdown, until_date)``. The DB-touching counterpart to
    :func:`render_weekly_digest`; imports live here so unit tests of the
    renderer don't require psycopg.
    """
    from genkei.cli.watchlist import _query_source_health, _with_health_status
    from genkei.experiments.signal_benchmark import (
        DEFAULT_CRYPTO_BENCHMARK,
        DEFAULT_EQUITY_BENCHMARK,
        compute_stack_benchmark_contexts,
    )
    from genkei.experiments.signal_rules import DEFAULT_RULES_PATH, load_rules
    from genkei.experiments.signal_store import detect_stacks, query_events

    generated_at = datetime.now(timezone.utc)
    until = until or generated_at.date()
    since = since or (until - timedelta(days=lookback_days))

    since_dt = datetime.combine(since, time(0, 0, tzinfo=timezone.utc))
    until_dt = datetime.combine(until, time(23, 59, 59, 999999, tzinfo=timezone.utc))

    rules = load_rules(rules_path or DEFAULT_RULES_PATH)
    event_since_dt = since_dt - timedelta(days=_max_rule_window_days(rules))
    events = query_events(since=event_since_dt, until=until_dt)
    stacks = _filter_stacks_for_digest_window(
        detect_stacks(events, rules),
        since=since_dt,
        until=until_dt,
    )

    bench_contexts = None
    if benchmark:
        bench_contexts = compute_stack_benchmark_contexts(
            stacks,
            equity_benchmark=DEFAULT_EQUITY_BENCHMARK,
            crypto_benchmark=DEFAULT_CRYPTO_BENCHMARK,
        )

    try:
        health_rows: list[dict[str, Any]] | None = _with_health_status(
            _query_source_health(), stale_hours=48.0
        )
    except Exception:  # pragma: no cover - health is best-effort context
        health_rows = None

    markdown = render_weekly_digest(
        stacks,
        benchmark_contexts=bench_contexts,
        health_rows=health_rows,
        since=since,
        until=until,
        generated_at=generated_at,
    )
    return markdown, until


def write_digest(markdown: str, until: date, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    """Write the digest to ``<output_dir>/weekly-<until>.md`` (idempotent)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"weekly-{until.isoformat()}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the weekly cross-source signal digest into reports/signals/."
    )
    parser.add_argument("--since", type=_parse_date, default=None)
    parser.add_argument("--until", type=_parse_date, default=None)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Window length when --since is omitted (default {DEFAULT_LOOKBACK_DAYS}).",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--no-benchmark",
        action="store_true",
        help="Skip the benchmark-adjusted column (no coinbase/yahoo price reads).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the digest to stdout instead of writing a file.",
    )
    args = parser.parse_args(argv)
    if args.since is not None and args.until is not None and args.since > args.until:
        parser.error("--since must be on or before --until")
    if args.lookback_days <= 0:
        parser.error("--lookback-days must be a positive integer")
    return args


def main(argv: list[str] | None = None) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    markdown, until = build_weekly_digest(
        since=args.since,
        until=args.until,
        lookback_days=args.lookback_days,
        benchmark=not args.no_benchmark,
    )
    if args.stdout:
        print(markdown)
    else:
        path = write_digest(markdown, until, output_dir=args.output_dir)
        print(f"Wrote weekly signal digest: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
