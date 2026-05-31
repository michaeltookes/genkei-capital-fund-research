"""13F crowding → signal_events emitter (B-093).

Second emitter wired into the cross-source correlator (B-064), after the
``insider_clusters`` reference emitter. It adapts B-061's ``compute_crowding``
output into atomic signal events. Because the correlator enforces
``min_distinct_sources >= 2``, this is the emitter that lets the engine fire
its *first* real multi-source stack: crowding + insider clusters both land on
equity-core assets, so ``activist_position_take`` and ``broad_exit`` become
fully fireable and ``smart_money_buy`` reaches two of its three sources.

Each ``CrowdingRow`` carries a quarter-over-quarter delta (``net_change`` =
holder count minus prior-period holder count). The *delta* is the signal — a
name jumping from 1 → 4 watchlist managers in a quarter is the
institutional-positioning analogue of an insider buy-cluster. A row maps to one
or more events keyed by the sign and magnitude of that delta:

* ``net_change > 0``                 → ``crowding_add``  (bullish)
* ``net_change >= jump_threshold``   → *also* ``crowding_jump``  (bullish)
* ``net_change < 0``                 → ``crowding_exit`` (bearish)
* ``net_change`` is 0 or ``None``    → no event (no actionable delta; ``None``
                                       is the first-observed period for a CUSIP,
                                       where no prior comparison exists)

A jump emits **both** ``crowding_add`` and ``crowding_jump`` rather than
replacing one with the other, because a big add *is* an add: the
``smart_money_buy`` rule consumes ``crowding_add`` while
``activist_position_take`` consumes ``crowding_jump``, and a +4 quarter should
participate in both. The two kinds share a ``source`` (``crowding``) and a
``source_ref``, so the correlator's per-source filtering keeps each rule reading
only its own kind — no within-rule double counting (see
``signal_store._filter_by_rule``).

Field mapping per event:

* ``asset``         = issuer ticker resolved from the CUSIP via the equity
                      watchlist; crowding on a non-watchlist CUSIP is logged +
                      skipped (the correlator's asset-grouping needs a stable
                      identifier and we don't leak raw CUSIPs into the events
                      table).
* ``asset_class``   = ``"equity"``.
* ``ts``            = ``period_of_report`` at UTC midnight (the quarter-end the
                      crowding is measured against).
* ``source``        = ``"crowding"``.
* ``signal_kind``   = ``crowding_add`` / ``crowding_jump`` / ``crowding_exit``.
* ``direction``     = ``bullish`` (add/jump) or ``bearish`` (exit).
* ``strength``      = ``min(abs(net_change) / 4, 1.0)`` — four net new (or lost)
                      watchlist managers in one quarter is full conviction; the
                      1 → 4 activist-add pattern saturates exactly there. The
                      constant lives in this module so a re-tune is obvious in
                      git history rather than buried in a faraway YAML.
* ``horizon``       = watchlist-derived equity sleeve tag (``equity:core``),
                      matching the rule horizons in ``signal_rules.yml``.
* ``source_ref``    = ``"<cusip>:<period_of_report_iso>"`` — the natural
                      identifier of the crowding observation. The UNIQUE
                      constraint on
                      ``(asset, ts, source, signal_kind, source_ref, horizon)``
                      makes re-emission idempotent; ``signal_kind`` is part of
                      that key, so add + jump for the same row are distinct rows.

Run as one ``meta.ingest_runs`` row tagged
``source='signal_emitter' endpoint='crowding'`` so the provenance trail is
uniform with the rest of the lake and ``genkei watchlist health`` surfaces
emitter staleness the same way it surfaces ingest staleness.

Loading note: ``compute_crowding`` derives each row's delta from the periods
present in the loaded positions, so the loader is deliberately *not* bounded on
``since`` — it pulls the full available history for the watchlist CUSIPs and
the emit window is applied afterward to ``period_of_report``. That guarantees
the prior-quarter comparison is always available even when the cron runs with a
tight rolling ``--since``.

The emitter skips 13F periods until the filing lag has elapsed. Without that
gate, a daily run could persist provisional add/exit events while managers are
still filing for the quarter; later upserts do not delete stale event kinds.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EquityEntry,
    Watchlist,
    load_watchlist,
)
from genkei.experiments.crowding_monitor import (
    CrowdingRow,
    compute_crowding,
    load_positions,
)
from genkei.experiments.signal_store import emit_signals_bulk

EMITTER_SOURCE = "crowding"
EMITTER_RUN_TAG = "signal_emitter"
EMITTER_ENDPOINT = "crowding"
# Net manager-count change that saturates strength at 1.0. Four net new (or
# lost) watchlist filers in a single quarter is the high-conviction signal; the
# canonical 1 → 4 activist-add pattern lands exactly at full strength.
STRENGTH_SATURATION_NET_CHANGE = Decimal("4")
# Net new entrants at/above which a row also emits a `crowding_jump` event.
# The `activist_position_take` rule consumes this kind; the YAML comment for
# that component reads "≥3 net new entrants (defined per emitter)".
DEFAULT_JUMP_THRESHOLD = 3
FORM_13F_FILING_LAG_DAYS = 45
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmitResult:
    """Return value of ``emit_recent_crowding`` for CLI / test inspection."""

    ingest_run_id: int
    events_emitted: int
    rows_skipped_no_ticker: int
    rows_skipped_no_delta: int
    rows_skipped_immature: int = 0


def _equities_by_cusip(watchlist: Watchlist) -> dict[str, list[EquityEntry]]:
    """Map each populated CUSIP to its watchlist equities (upper-cased key)."""
    out: dict[str, list[EquityEntry]] = defaultdict(list)
    for entry in watchlist.equities:
        if entry.cusip:
            out[entry.cusip.upper()].append(entry)
    return dict(out)


def _strength_from_net_change(net_change: int) -> Decimal:
    """Map a holder-count delta to a 0-1 strength via a saturating ramp.

    Uses ``abs`` so exits scale the same way adds do: ±1 → 0.25, ±2 → 0.5,
    ±4 → 1.0, anything beyond saturates at 1.0. The saturation constant lives
    in this module so a re-tune shows up in git blame here.
    """
    if net_change == 0:
        return Decimal("0")
    value = Decimal(abs(net_change)) / STRENGTH_SATURATION_NET_CHANGE
    return value if value < Decimal("1") else Decimal("1")


def _classify_kinds(
    net_change: int | None, *, jump_threshold: int
) -> list[tuple[str, str]]:
    """Return the ``(signal_kind, direction)`` pairs a row should emit.

    Empty when there's no actionable delta (``net_change`` is ``None`` —
    first-observed period — or 0). A positive change emits ``crowding_add``
    and, when it clears ``jump_threshold``, *also* ``crowding_jump``. A
    negative change emits ``crowding_exit``.
    """
    if net_change is None or net_change == 0:
        return []
    if net_change > 0:
        kinds = [("crowding_add", "bullish")]
        if net_change >= jump_threshold:
            kinds.append(("crowding_jump", "bullish"))
        return kinds
    return [("crowding_exit", "bearish")]


def _period_to_ts(period: date) -> datetime:
    """Convert a quarter-end *date* to a UTC datetime for the events table."""
    return datetime.combine(period, time(0, 0, tzinfo=timezone.utc))


def _horizon_tag(equity: EquityEntry) -> str:
    return f"equity:{equity.sleeve}"


def _build_events(
    row: CrowdingRow,
    equities_by_cusip: dict[str, list[EquityEntry]],
    *,
    jump_threshold: int,
) -> list[dict[str, Any]]:
    """Map one ``CrowdingRow`` to signal events.

    One event per (watchlist ticker for the CUSIP) × (signal kind the delta
    triggers). Returns ``[]`` when the CUSIP isn't on the watchlist *or* when
    the row carries no actionable delta — the caller distinguishes the two via
    ``_classify_kinds`` so it can count skips separately.
    """
    kinds = _classify_kinds(row.net_change, jump_threshold=jump_threshold)
    if not kinds:
        return []
    equities = equities_by_cusip.get(row.cusip.upper())
    if not equities:
        LOGGER.warning(
            "crowding row for CUSIP %s (period %s) not in equity watchlist; "
            "skipping signal emission",
            row.cusip,
            row.period_of_report.isoformat(),
        )
        return []

    ts = _period_to_ts(row.period_of_report)
    source_ref = f"{row.cusip.upper()}:{row.period_of_report.isoformat()}"
    # net_change is non-None here (kinds is non-empty), so strength is well-defined.
    strength = _strength_from_net_change(row.net_change or 0)
    events: list[dict[str, Any]] = []
    for equity in equities:
        payload: dict[str, Any] = {
            "cusip": row.cusip,
            "ticker": equity.symbol,
            "sleeve": equity.sleeve,
            "tier": equity.tier,
            "issuer_name": row.issuer_name,
            "period_of_report": row.period_of_report.isoformat(),
            "holder_count": row.holder_count,
            "prior_holder_count": row.prior_holder_count,
            "net_change": row.net_change,
            "new_entrants": list(row.new_entrants),
            "exits": list(row.exits),
            "holder_ciks": list(row.holder_ciks),
            "holder_names": list(row.holder_names),
            "total_value_usd": (
                str(row.total_value_usd) if row.total_value_usd is not None else None
            ),
            "total_shares": (
                str(row.total_shares) if row.total_shares is not None else None
            ),
        }
        for signal_kind, direction in kinds:
            events.append(
                {
                    "asset": equity.symbol,
                    "asset_class": "equity",
                    "horizon": _horizon_tag(equity),
                    "ts": ts,
                    "source": EMITTER_SOURCE,
                    "signal_kind": signal_kind,
                    "direction": direction,
                    "strength": strength,
                    "payload": payload,
                    "source_ref": source_ref,
                }
            )
    return events


def emit_recent_crowding(
    *,
    since: date | None = None,
    until: date | None = None,
    jump_threshold: int = DEFAULT_JUMP_THRESHOLD,
    config: Path = DEFAULT_WATCHLIST_PATH,
    as_of: date | None = None,
) -> EmitResult:
    """Compute crowding deltas and emit signal events for the date range.

    ``since`` / ``until`` bound which ``period_of_report`` rows are *emitted*;
    the underlying positions are loaded without a ``since`` bound so each row's
    prior-quarter delta is always computable. Runs in a single
    ``meta.ingest_runs`` row so the emitter is queryable via
    ``genkei watchlist health`` like any other source.
    """
    effective_as_of = as_of or datetime.now(timezone.utc).date()
    mature_through = effective_as_of - timedelta(days=FORM_13F_FILING_LAG_DAYS)
    watchlist = load_watchlist(config)
    equities_by_cusip = _equities_by_cusip(watchlist)
    cusips = sorted(equities_by_cusip)

    with db.ingest_run(
        EMITTER_RUN_TAG,
        endpoint=EMITTER_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "jump_threshold": jump_threshold,
            "as_of": effective_as_of.isoformat(),
            "mature_through": mature_through.isoformat(),
            "watchlist_cusips": len(cusips),
        },
    ) as run:
        if not cusips:
            LOGGER.warning(
                "no watchlist equities carry a CUSIP; crowding emitter has "
                "nothing to scope to (populate `cusip:` fields in watchlists.yml)"
            )
            return EmitResult(
                ingest_run_id=run.id,
                events_emitted=0,
                rows_skipped_no_ticker=0,
                rows_skipped_no_delta=0,
            )

        # No `since` on the load: compute_crowding needs the prior period to
        # derive each row's delta. `until` is safe to push down — a quarter's
        # delta only depends on periods at or before it.
        positions = load_positions(cusips=cusips, until=until)
        rows = compute_crowding(positions)

        events: list[dict[str, Any]] = []
        skipped_no_ticker = 0
        skipped_no_delta = 0
        skipped_immature = 0
        for row in rows:
            if since is not None and row.period_of_report < since:
                continue
            if until is not None and row.period_of_report > until:
                continue
            if row.period_of_report > mature_through:
                skipped_immature += 1
                continue
            if not _classify_kinds(row.net_change, jump_threshold=jump_threshold):
                skipped_no_delta += 1
                continue
            built = _build_events(
                row, equities_by_cusip, jump_threshold=jump_threshold
            )
            if not built:
                # Delta existed but the CUSIP isn't on the watchlist. With the
                # load scoped to watchlist CUSIPs this is rare, but a CUSIP can
                # appear in holdings under an old identifier — count it loudly.
                skipped_no_ticker += 1
                continue
            events.extend(built)

        rows_written = emit_signals_bulk(events, ingest_run_id=run.id)
        run.add_rows(rows_written)
        return EmitResult(
            ingest_run_id=run.id,
            events_emitted=rows_written,
            rows_skipped_no_ticker=skipped_no_ticker,
            rows_skipped_no_delta=skipped_no_delta,
            rows_skipped_immature=skipped_immature,
        )


def parse_args(argv: list[str]) -> Any:
    import argparse

    def parse_date_arg(label: str) -> Any:
        def parse(raw: str) -> date | None:
            try:
                return _parse_date(raw, label=label)
            except Exception as exc:
                raise argparse.ArgumentTypeError(str(exc)) from exc

        return parse

    parser = argparse.ArgumentParser(
        description="Emit 13F crowding signal events into meta.signal_events."
    )
    parser.add_argument("--since", type=parse_date_arg("since"), default=None)
    parser.add_argument("--until", type=parse_date_arg("until"), default=None)
    parser.add_argument(
        "--jump-threshold",
        type=int,
        default=DEFAULT_JUMP_THRESHOLD,
        help="net new entrants at/above which a row also emits crowding_jump",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    result = emit_recent_crowding(
        since=args.since,
        until=args.until,
        jump_threshold=args.jump_threshold,
    )
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "events_emitted": result.events_emitted,
                    "rows_skipped_no_ticker": result.rows_skipped_no_ticker,
                    "rows_skipped_no_delta": result.rows_skipped_no_delta,
                    "rows_skipped_immature": result.rows_skipped_immature,
                    "source": EMITTER_SOURCE,
                },
                default=_json_default,
            )
        )
    else:
        print(
            f"crowding emitter wrote ingest_run_id={result.ingest_run_id} "
            f"events={result.events_emitted} "
            f"skipped_no_ticker={result.rows_skipped_no_ticker} "
            f"skipped_no_delta={result.rows_skipped_no_delta} "
            f"skipped_immature={result.rows_skipped_immature}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
