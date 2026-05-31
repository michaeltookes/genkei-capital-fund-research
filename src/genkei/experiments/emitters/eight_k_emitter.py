"""8-K filing impact → signal_events emitter (B-094).

Third emitter wired into the cross-source correlator (B-064), after the
``insider_clusters`` (B-064) and ``crowding`` (B-093) emitters. It adapts
B-057's ``FilingEvent`` output (one row per 8-K filing on a watchlist issuer,
with parsed item codes) into atomic signal events. Completes the component
coverage for ``smart_money_buy`` (insider + crowding + 8-K item 1.01/2.02
within 7d) and ``deterioration_stack`` (sell cluster + 8-K item 5.02/4.02
within 30d) — both rules now have every component live.

Each ``FilingEvent`` fans out to **one signal event per item code** in its
``items`` field. A filing with ``items = "2.02,9.01"`` emits two events
(one ``item_2_02``, one ``item_9_01``), each with the item-code-specific
direction and strength. The two events share an ``accession_number``-based
``source_ref`` but differ on ``signal_kind``, so they coexist as distinct
rows under the UNIQUE key
``(asset, ts, source, signal_kind, source_ref, horizon)``.

No separate "any-8-K baseline" event is emitted: a rule that wants any-8-K
matching can declare a ``signal_kind: null`` wildcard component, which the
correlator already matches against per-item events without double-counting.
Emitting both a baseline AND per-item events would double-count the same
filing whenever a rule had both a wildcard and an exact-kind component.

Field mapping per event:

* ``asset``         = ticker resolved via the watchlist by CIK lookup
                      (``load_filing_events`` already fans across shared
                      CIKs like GOOG/GOOGL).
* ``asset_class``   = ``"equity"``.
* ``ts``            = ``event_date`` (the after-hours-adjusted next-
                      trading-day from B-057's ``_event_anchor_date``) at
                      UTC midnight. More honest than ``filed_at`` when a
                      filing lands after 4pm ET or on a weekend — the
                      event becomes actionable at the next open.
* ``source``        = ``"eight_k_impact"`` (matches ``signal_rules.yml``).
* ``signal_kind``   = ``f"item_{major}_{minor}"`` for canonical codes
                      (``"1.01"`` → ``"item_1_01"``); legacy pre-2009
                      dot-less codes pass through as ``"item_<code>"``.
* ``direction``     = item-code-conditional via ``ITEM_CODE_PROFILES``.
                      ``bullish`` for the rules-YAML-curated bullish codes
                      (1.01, 2.02), ``bearish`` for the curated bearish
                      codes (4.02, 5.02) and other clearly-negative codes
                      (1.02, 2.06, 3.01), ``neutral`` otherwise.
* ``strength``      = item-code-conditional via ``ITEM_CODE_PROFILES``.
                      Reflects intrinsic informativeness of the item
                      class — 4.02 (non-reliance) is rare and consequential
                      (0.9), 9.01 (exhibits) is almost always routine and
                      co-filed (0.2). The table lives in this module so
                      a re-tune is obvious in git blame here rather than
                      buried in a faraway YAML.
* ``horizon``       = watchlist-derived equity sleeve tag
                      (``equity:<sleeve>``, e.g. ``equity:core``).
* ``source_ref``    = ``accession_number`` — the natural identifier of the
                      filing globally unique in EDGAR. ``signal_kind`` is
                      part of the UNIQUE key, so per-item-code events for
                      the same filing coexist as distinct rows under the
                      same source_ref. Re-runs against the same filings
                      are idempotent.

Run as one ``meta.ingest_runs`` row tagged
``source='signal_emitter' endpoint='eight_k_impact'`` so the provenance
trail is uniform with the rest of the lake and ``genkei watchlist health``
surfaces emitter staleness the same way it surfaces ingest staleness.

8-Ks are atomic — filed within 4 business days of the event per SEC rule —
so there is no analogous-to-13F maturity gate. Each filing is complete on
arrival; amendments are filed as separate 8-K/A filings with their own
accession numbers, which the emitter treats as new events. Filings without
any parseable item codes are logged + skipped: they're rare (test filings,
schema errors) and emitting them with no signal_kind would lose the per-
item discriminator the correlator needs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist
from genkei.experiments.eight_k_impact import FilingEvent, load_filing_events
from genkei.experiments.signal_store import emit_signals_bulk

EMITTER_SOURCE = "eight_k_impact"
EMITTER_RUN_TAG = "signal_emitter"
EMITTER_ENDPOINT = "eight_k_impact"

# Per-item-code direction + strength. Strength reflects the intrinsic
# informativeness of the item class — 4.02 (non-reliance on prior
# financials) is the rarest and most consequential 8-K code, 9.01
# (exhibits) is almost always routine and co-filed with the actual
# substantive item. Direction matches the rules YAML's curated bullish
# (1.01, 2.02) and bearish (4.02, 5.02) assignments where they exist;
# other codes use a conservative direction based on the SEC's own
# language for what the item discloses. Uncurated codes default to
# neutral so they're queryable + future-rule-eligible without faking
# a directional bias the data doesn't support.
ITEM_CODE_PROFILES: dict[str, tuple[str, Decimal]] = {
    "1.01": ("bullish", Decimal("0.6")),  # Material Definitive Agreement
    "1.02": ("bearish", Decimal("0.6")),  # Termination of Material Agreement
    "1.03": ("bearish", Decimal("0.9")),  # Bankruptcy or Receivership
    "2.01": ("neutral", Decimal("0.6")),  # Acquisition or Disposition of Assets
    "2.02": ("bullish", Decimal("0.5")),  # Results of Operations / earnings
    "2.03": ("bearish", Decimal("0.5")),  # Material Direct Financial Obligation
    "2.04": ("bearish", Decimal("0.7")),  # Triggering Event Accelerates Obligation
    "2.05": ("bearish", Decimal("0.6")),  # Costs Associated with Exit / Disposal
    "2.06": ("bearish", Decimal("0.7")),  # Material Impairments
    "3.01": ("bearish", Decimal("0.7")),  # Notice of Delisting / Listing Standards
    "3.02": ("neutral", Decimal("0.4")),  # Unregistered Sales of Equity
    "3.03": ("neutral", Decimal("0.4")),  # Material Modification to Rights of Holders
    "4.01": ("neutral", Decimal("0.5")),  # Changes in Registrant's Certifying Accountant
    "4.02": ("bearish", Decimal("0.9")),  # Non-reliance on prior financials
    "5.01": ("neutral", Decimal("0.6")),  # Changes in Control of Registrant
    "5.02": ("bearish", Decimal("0.7")),  # Officer Departures / Appointments
    "5.03": ("neutral", Decimal("0.3")),  # Amendments to Articles / Bylaws
    "5.07": ("neutral", Decimal("0.3")),  # Submission of Matters to Shareholder Vote
    "5.08": ("neutral", Decimal("0.3")),  # Shareholder Director Nominations
    "7.01": ("neutral", Decimal("0.4")),  # Reg FD Disclosure
    "8.01": ("neutral", Decimal("0.4")),  # Other Events
    "9.01": ("neutral", Decimal("0.2")),  # Financial Statements and Exhibits
}
DEFAULT_ITEM_PROFILE: tuple[str, Decimal] = ("neutral", Decimal("0.3"))
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmitResult:
    """Return value of ``emit_recent_filings`` for CLI / test inspection."""

    ingest_run_id: int
    events_emitted: int
    filings_seen: int
    filings_skipped_no_items: int


def _signal_kind_for_item(item_code: str) -> str:
    """Convert SEC item-code text to the rules-YAML ``signal_kind`` convention.

    Canonical codes like ``"1.01"`` become ``"item_1_01"``. Legacy pre-2009
    dot-less codes like ``"5"`` become ``"item_5"``. Whitespace was already
    stripped upstream by ``eight_k_impact.parse_item_codes``.
    """
    return f"item_{item_code.replace('.', '_')}"


def _profile_for_item(item_code: str) -> tuple[str, Decimal]:
    """Return ``(direction, strength)`` for an item code, defaulting neutral."""
    return ITEM_CODE_PROFILES.get(item_code, DEFAULT_ITEM_PROFILE)


def _event_ts(event: FilingEvent) -> datetime:
    """Convert the after-hours-adjusted ``event_date`` to a UTC midnight datetime."""
    return datetime.combine(event.event_date, time(0, 0, tzinfo=timezone.utc))


def _horizon_tag_for_sleeve(sleeve: str) -> str:
    return f"equity:{sleeve}"


def _sleeve_by_ticker(config: Path) -> dict[str, str]:
    """Map watchlist ticker → sleeve for horizon tagging."""
    watchlist = load_watchlist(config)
    return {entry.symbol: entry.sleeve for entry in watchlist.equities}


def _build_events(
    filing: FilingEvent,
    *,
    sleeve_by_ticker: dict[str, str],
) -> list[dict[str, Any]]:
    """Map one filing to one event per item code.

    Returns ``[]`` when ``items`` is empty (rare; logged at the caller).
    The sleeve lookup falls back to ``"core"`` if the ticker isn't in the
    map — defensive (``load_filing_events`` restricts to watchlist tickers
    in practice) but keeps the emitter from crashing on a config edge case.
    """
    if not filing.item_codes:
        return []
    sleeve = sleeve_by_ticker.get(filing.ticker, "core")
    horizon = _horizon_tag_for_sleeve(sleeve)
    ts = _event_ts(filing)
    events: list[dict[str, Any]] = []
    for code in filing.item_codes:
        direction, strength = _profile_for_item(code)
        signal_kind = _signal_kind_for_item(code)
        payload: dict[str, Any] = {
            "ticker": filing.ticker,
            "cik": filing.cik,
            "sleeve": sleeve,
            "accession_number": filing.accession_number,
            "filed_at": filing.filed_at.isoformat(),
            "event_date": filing.event_date.isoformat(),
            "item_code": code,
            "all_item_codes": list(filing.item_codes),
            "accepted_at": (
                filing.accepted_at.isoformat() if filing.accepted_at else None
            ),
        }
        events.append(
            {
                "asset": filing.ticker,
                "asset_class": "equity",
                "horizon": horizon,
                "ts": ts,
                "source": EMITTER_SOURCE,
                "signal_kind": signal_kind,
                "direction": direction,
                "strength": strength,
                "payload": payload,
                "source_ref": filing.accession_number,
            }
        )
    return events


def emit_recent_filings(
    *,
    since: date | None = None,
    until: date | None = None,
    config: Path = DEFAULT_WATCHLIST_PATH,
) -> EmitResult:
    """Load 8-K filings in the date range and emit signal events.

    Runs in a single ``meta.ingest_runs`` row so the emitter's progress is
    queryable via ``genkei watchlist health`` like any other source.
    """
    sleeve_by_ticker = _sleeve_by_ticker(config)

    with db.ingest_run(
        EMITTER_RUN_TAG,
        endpoint=EMITTER_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "watchlist_tickers": len(sleeve_by_ticker),
        },
    ) as run:
        filings = load_filing_events(since=since, until=until)
        events: list[dict[str, Any]] = []
        skipped_no_items = 0
        for filing in filings:
            built = _build_events(filing, sleeve_by_ticker=sleeve_by_ticker)
            if not built:
                LOGGER.warning(
                    "8-K filing %s (CIK %s) has no parseable item codes; "
                    "skipping signal emission",
                    filing.accession_number,
                    filing.cik,
                )
                skipped_no_items += 1
                continue
            events.extend(built)

        rows_written = emit_signals_bulk(events, ingest_run_id=run.id)
        run.add_rows(rows_written)
        return EmitResult(
            ingest_run_id=run.id,
            events_emitted=rows_written,
            filings_seen=len(filings),
            filings_skipped_no_items=skipped_no_items,
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
        description="Emit 8-K impact signal events into meta.signal_events."
    )
    parser.add_argument("--since", type=parse_date_arg("since"), default=None)
    parser.add_argument("--until", type=parse_date_arg("until"), default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    result = emit_recent_filings(since=args.since, until=args.until)
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "events_emitted": result.events_emitted,
                    "filings_seen": result.filings_seen,
                    "filings_skipped_no_items": result.filings_skipped_no_items,
                    "source": EMITTER_SOURCE,
                },
                default=_json_default,
            )
        )
    else:
        print(
            f"8-K emitter wrote ingest_run_id={result.ingest_run_id} "
            f"events={result.events_emitted} "
            f"filings={result.filings_seen} "
            f"skipped_no_items={result.filings_skipped_no_items}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
