"""8-K filing impact event study (B-057).

Phase 5 experiment that asks: *does an 8-K filing predict short-run
price drift in the issuer's stock?* Unblocked by B-092 (Yahoo equity
prices on 2026-05-24) — until then the SEC half of the join existed
but the equity half didn't.

**Question framing.** For every 8-K filing the lake has carried since
1994 (across the 25 watchlist issuers that file with SEC), compute
return windows around the filing date — pre-event drift, same-day
return, post-event short / long drift — and aggregate. Stratify by
8-K item code (Item 2.02 earnings releases vs Item 5.07 routine
shareholder votes vs Item 5.02 officer departures) and by macro
regime (B-059's analytics.macro_regime_per_date) so the reader can
see whether the 8-K's effect is incremental to baseline conditions.

**Why event-study, not classifier.** B-058 fit a threshold classifier
to TVL features predicting future price drawdowns. This experiment
is the inverse: we already know the event (the 8-K was filed) and
ask whether the price moved abnormally around it. Event-study
aggregation (mean / median / hit rate by stratum) is the natural
framing.

**Why no SPY benchmark in v1.** The standard "abnormal return" in
event studies is asset return − benchmark return (SPY or sector).
The lake doesn't carry SPY today (adding it is a 1-minute follow-up
but inflates scope). v1 reports raw returns plus an optional macro-
regime stratification — when the regime is `risk_off` the baseline
expected return is lower anyway, so the regime split captures most
of what benchmark adjustment would. v2 adds SPY when somebody cares.

**Return computation.** Date-window-based (NOT bar-count). For an
8-K filed on date T, we look up Yahoo close prices in the window
``[T - max_lookback, T + max_forward]`` and pick the trading days
nearest each window boundary via bisect. Windows shift to the
following trading day when the filing date itself is a weekend
or holiday.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from genkei.common import db

# Default return windows. Tuples are (label, days_offset_low,
# days_offset_high). For a filing on date T:
#   pre_5d  = return from close[T-6] to close[T-1] (5 trading days before)
#   same_day = return from close[T-1] to close[T] (the filing day itself)
#   post_1d = return from close[T] to close[T+1]
#   post_5d = return from close[T] to close[T+5]
#   post_30d = return from close[T] to close[T+30]
DEFAULT_WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("pre_5d", -6, -1),
    ("same_day", -1, 0),
    ("post_1d", 0, 1),
    ("post_5d", 0, 5),
    ("post_30d", 0, 30),
)
DEFAULT_HORIZON = "equity:core"

# The widest window we need history for. Used as a buffer when
# loading the price series around an event date.
MAX_LOOKBACK_DAYS = 14
MAX_FORWARD_DAYS = 45
BOUNDARY_CUSHION_DAYS = 7
EVENT_ANCHOR_PREFILTER_DAYS = 7
MARKET_CLOSE_ET = time(16, 0)
EASTERN_TZ = ZoneInfo("America/New_York")
SPECIAL_MARKET_CLOSURES = {
    date(1994, 4, 27),  # Richard Nixon funeral
    date(2001, 9, 11),
    date(2001, 9, 12),
    date(2001, 9, 13),
    date(2001, 9, 14),
    date(2004, 6, 11),  # Ronald Reagan funeral
    date(2007, 1, 2),  # Gerald Ford funeral
    date(2012, 10, 29),  # Hurricane Sandy
    date(2012, 10, 30),
    date(2018, 12, 5),  # George H.W. Bush funeral
    date(2025, 1, 9),  # Jimmy Carter funeral
}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    candidate = date(year, month, 1)
    offset = (weekday - candidate.weekday()) % 7
    return candidate + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        candidate = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        candidate = date(year, month + 1, 1) - timedelta(days=1)
    return candidate - timedelta(days=(candidate.weekday() - weekday) % 7)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * offset) // 451
    month = (h + offset - 7 * m + 114) // 31
    day = ((h + offset - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _market_holidays(year: int) -> set[date]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 2, 0, 3),
        _easter_date(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(year, 12, 25),
        _observed_fixed_holiday(year + 1, 1, 1),
    }
    if year >= 1998:
        holidays.add(_nth_weekday(year, 1, 0, 3))
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(year, 6, 19))
    return {day for day in holidays if day.year == year} | {
        day for day in SPECIAL_MARKET_CLOSURES if day.year == year
    }


def _is_trading_day(anchor: date) -> bool:
    return anchor.weekday() < 5 and anchor not in _market_holidays(anchor.year)


def _next_trading_day(anchor: date) -> date:
    while not _is_trading_day(anchor):
        anchor += timedelta(days=1)
    return anchor


def _event_anchor_date(filed_at: date, accepted_at: datetime | None) -> date:
    if accepted_at is None:
        return _next_trading_day(filed_at)
    if accepted_at.tzinfo is None:
        accepted_at = accepted_at.replace(tzinfo=timezone.utc)
    accepted_et = accepted_at.astimezone(EASTERN_TZ)
    anchor = accepted_et.date()
    if accepted_et.time() >= MARKET_CLOSE_ET:
        anchor += timedelta(days=1)
    return _next_trading_day(anchor)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingEvent:
    """One 8-K filing event keyed back to the watchlist issuer."""

    ticker: str
    cik: str
    filed_at: date
    accession_number: str
    item_codes: tuple[str, ...]  # parsed from sec.filings.items
    accepted_at: datetime | None = None

    @property
    def event_date(self) -> date:
        return _event_anchor_date(self.filed_at, self.accepted_at)


@dataclass(frozen=True)
class PricePoint:
    """One day's adjusted close for an issuer."""

    ts: date
    adj_close: Decimal


@dataclass(frozen=True)
class EventReturns:
    """Per-window return for a single filing event.

    Each field is a percentage (e.g. +2.5 = 2.5% gain). ``None``
    when the price series doesn't fully cover the window (event
    near the start / end of the available history).
    """

    event: FilingEvent
    windows: dict[str, Decimal | None]
    regime: str | None  # from analytics.macro_regime_per_date (may be None pre-2006)
    horizon: str = DEFAULT_HORIZON


@dataclass(frozen=True)
class StratumStats:
    """Aggregated stats for one stratum (ticker / item / regime / overall)."""

    stratum_key: str
    n_events: int
    horizon: str = DEFAULT_HORIZON
    # Per-window stats — same labels as the DEFAULT_WINDOWS keys.
    mean_pct: dict[str, Decimal | None] = field(default_factory=dict)
    median_pct: dict[str, Decimal | None] = field(default_factory=dict)
    hit_rate_pct: dict[str, Decimal | None] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_item_codes(raw: str | None) -> tuple[str, ...]:
    """Parse SEC's comma-separated 8-K item code field.

    Handles single codes (``"7.01"``), comma-separated lists
    (``"7.01,9.01"``), surrounding whitespace, and the legacy
    pre-2009 dot-less format (``"5"`` instead of ``"5.01"``). Drops
    empty / whitespace-only entries.
    """
    if raw is None:
        return ()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(parts)


def _price_at_or_before(
    prices: Sequence[PricePoint],
    target: date,
    *,
    dates: Sequence[date] | None = None,
) -> Decimal | None:
    """Return the close on-or-before ``target`` (the most recent
    trading day with a price ≤ target).

    Uses bisect for O(log n) lookup — the price series is daily and
    sorted ascending by ts.
    """
    if not prices:
        return None
    # bisect_right gives us insertion point AFTER any equal element;
    # subtract 1 to get the last index ≤ target.
    dates = dates if dates is not None else [p.ts for p in prices]
    idx = bisect_right(dates, target) - 1
    if idx < 0:
        return None
    return prices[idx].adj_close


def _price_at_or_after(
    prices: Sequence[PricePoint],
    target: date,
    *,
    dates: Sequence[date] | None = None,
) -> Decimal | None:
    """Return the close on-or-after ``target``."""
    if not prices:
        return None
    dates = dates if dates is not None else [p.ts for p in prices]
    idx = bisect_left(dates, target)
    if idx >= len(prices):
        return None
    return prices[idx].adj_close


def _pct_return(start: Decimal | None, end: Decimal | None) -> Decimal | None:
    if start is None or end is None or start == 0:
        return None
    return Decimal("100") * (end - start) / start


def compute_windowed_returns(
    prices: Sequence[PricePoint],
    *,
    event_date: date,
    windows: Sequence[tuple[str, int, int]] = DEFAULT_WINDOWS,
) -> dict[str, Decimal | None]:
    """For each (label, lo, hi) window, compute return from close at
    ``event_date + lo`` to close at ``event_date + hi``.

    For the "start" date (offset lo) we pick the close on-or-before
    that date (so a Friday filing's "same_day" window uses Thursday
    as the prior close). For post-event "end" dates we pick the close
    on-or-after (so a Friday filing's "post_1d" uses Monday). For
    pre-event windows, the end date also uses on-or-before so the
    pre-window cannot include the filing-day move.
    """
    out: dict[str, Decimal | None] = {}
    dates = [p.ts for p in prices]
    for label, lo, hi in windows:
        start_target = event_date + timedelta(days=lo)
        end_target = event_date + timedelta(days=hi)
        start_price = _price_at_or_before(prices, start_target, dates=dates)
        if hi < 0:
            end_price = _price_at_or_before(prices, end_target, dates=dates)
        else:
            end_price = _price_at_or_after(prices, end_target, dates=dates)
        out[label] = _pct_return(start_price, end_price)
    return out


# ---------------------------------------------------------------------------
# Pure aggregation
# ---------------------------------------------------------------------------


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / Decimal("2")


def _hit_rate(values: Sequence[Decimal]) -> Decimal | None:
    """Share of values that are strictly positive, as a percent."""
    if not values:
        return None
    hits = sum(1 for v in values if v > 0)
    return Decimal("100") * Decimal(hits) / Decimal(len(values))


def aggregate(
    event_returns: Sequence[EventReturns],
    *,
    stratum_key: str = "all",
    windows: Sequence[tuple[str, int, int]] = DEFAULT_WINDOWS,
) -> StratumStats:
    """Aggregate a group of event-return rows into a single stats row.

    Each window aggregates independently. Events with NULL in a given
    window are dropped from that window's stats only (we keep them
    contributing to other windows).
    """
    n = len(event_returns)
    horizon = event_returns[0].horizon if event_returns else DEFAULT_HORIZON
    mean_pct: dict[str, Decimal | None] = {}
    median_pct: dict[str, Decimal | None] = {}
    hit_rate_pct: dict[str, Decimal | None] = {}
    for label, _, _ in windows:
        vals = [e.windows.get(label) for e in event_returns]
        non_null = [v for v in vals if v is not None]
        mean_pct[label] = _mean(non_null)
        median_pct[label] = _median(non_null)
        hit_rate_pct[label] = _hit_rate(non_null)
    return StratumStats(
        stratum_key=stratum_key,
        n_events=n,
        horizon=horizon,
        mean_pct=mean_pct,
        median_pct=median_pct,
        hit_rate_pct=hit_rate_pct,
    )


# ---------------------------------------------------------------------------
# Lake loaders
# ---------------------------------------------------------------------------


def load_filing_events(
    *,
    ticker: str | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[FilingEvent]:
    """Load 8-K events from sec.filings joined to the watchlist via CIK.

    Filters: ``ticker`` restricts to one issuer; ``since`` / ``until``
    bound the filing date range. We resolve ticker via the watchlist
    in Python (the EquityEntry's symbol → cik map) rather than in
    SQL because the watchlist is the source of truth for which CIKs
    we care about.
    """
    from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist

    watchlist = load_watchlist(DEFAULT_WATCHLIST_PATH)
    # Build cik → tickers map. Skip equities without CIKs (ETFs / new
    # entries) — they have no 8-K filings to match anyway.
    cik_to_tickers: dict[str, list[str]] = {}
    for entry in watchlist.equities:
        if entry.cik:
            cik_to_tickers.setdefault(entry.cik, []).append(entry.symbol)
    if ticker is not None:
        wanted = ticker.upper()
        cik_to_tickers = {
            cik: [symbol for symbol in tickers if symbol.upper() == wanted]
            for cik, tickers in cik_to_tickers.items()
        }
        cik_to_tickers = {cik: tickers for cik, tickers in cik_to_tickers.items() if tickers}
    if not cik_to_tickers:
        return []
    ciks = tuple(cik_to_tickers.keys())

    sql = (
        "SELECT cik, filed_at::date AS d, accepted_at, accession_number, items "
        "FROM sec.filings WHERE form_type = '8-K' AND cik = ANY(%s)"
    )
    params: list[Any] = [list(ciks)]
    if since is not None:
        sql += " AND filed_at::date >= %s"
        # Market closures can shift event_date after filed_at; the Python
        # filter below enforces the exact user-facing date bound.
        params.append(since - timedelta(days=EVENT_ANCHOR_PREFILTER_DAYS))
    if until is not None:
        sql += " AND filed_at::date <= %s"
        params.append(until)
    sql += " ORDER BY filed_at ASC"

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    events: list[FilingEvent] = []
    for cik, filed_at, accepted_at, accession, items in rows:
        event_date = _event_anchor_date(filed_at, accepted_at)
        if since is not None and event_date < since:
            continue
        if until is not None and event_date > until:
            continue
        for symbol in cik_to_tickers[cik]:
            events.append(
                FilingEvent(
                    ticker=symbol,
                    cik=cik,
                    filed_at=filed_at,
                    accession_number=accession,
                    item_codes=parse_item_codes(items),
                    accepted_at=accepted_at,
                )
            )
    return events


def load_price_series(
    ticker: str,
    *,
    since: date,
    until: date,
) -> list[PricePoint]:
    """Load adj_close per day from yahoo.candles, ascending by ts.

    Falls back to unadjusted ``close`` if ``adj_close`` is NULL (rare,
    only for very-new IPOs). Returns sorted ascending so the bisect-
    based lookups in ``compute_windowed_returns`` work directly.
    """
    sql = (
        "SELECT ts::date AS d, COALESCE(adj_close, close) AS price "
        "FROM yahoo.candles "
        "WHERE ticker = %s AND ts::date >= %s AND ts::date <= %s "
        "ORDER BY ts ASC"
    )
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, [ticker, since, until])
        rows = cur.fetchall()
    return [PricePoint(ts=ts, adj_close=Decimal(price)) for ts, price in rows]


def load_regime_for_dates(dates: Sequence[date]) -> dict[date, str]:
    """Bulk-fetch the regime label for each given date.

    Returns ``{date: regime}``. Dates outside coverage (pre-2006 or
    after the view's latest ts) are absent from the result.
    """
    if not dates:
        return {}
    unique_dates = sorted(set(dates))
    sql = (
        "SELECT ts, regime FROM analytics.macro_regime_per_date "
        "WHERE ts = ANY(%s)"
    )
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, [unique_dates])
        rows = cur.fetchall()
    return {ts: regime for ts, regime in rows}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_event_study(
    *,
    ticker: str | None = None,
    since: date | None = None,
    until: date | None = None,
    windows: Sequence[tuple[str, int, int]] = DEFAULT_WINDOWS,
) -> list[EventReturns]:
    """Top-level: load events + prices + regimes, compute per-event returns.

    Returns one ``EventReturns`` per 8-K filing in scope. Caller
    aggregates by ticker / item / regime as needed.
    """
    events = load_filing_events(ticker=ticker, since=since, until=until)
    if not events:
        return []
    required_lookback = max(
        (max(-lo, -hi, 0) for _, lo, hi in windows), default=0
    )
    required_forward = max((max(lo, hi, 0) for _, lo, hi in windows), default=0)
    lookback_days = max(MAX_LOOKBACK_DAYS, required_lookback + BOUNDARY_CUSHION_DAYS)
    forward_days = max(MAX_FORWARD_DAYS, required_forward + BOUNDARY_CUSHION_DAYS)

    # Group events by ticker so we load each ticker's price series once.
    by_ticker: dict[str, list[FilingEvent]] = {}
    for e in events:
        by_ticker.setdefault(e.ticker, []).append(e)

    regimes = load_regime_for_dates([e.event_date for e in events])

    out: list[EventReturns] = []
    for t, ticker_events in by_ticker.items():
        # Pad the price series so the widest window's lookback /
        # lookahead fits on the edges.
        first_event = min(e.event_date for e in ticker_events)
        last_event = max(e.event_date for e in ticker_events)
        prices = load_price_series(
            t,
            since=first_event - timedelta(days=lookback_days),
            until=last_event + timedelta(days=forward_days),
        )
        for event in ticker_events:
            windows_dict = compute_windowed_returns(
                prices, event_date=event.event_date, windows=windows
            )
            out.append(
                EventReturns(
                    event=event,
                    windows=windows_dict,
                    regime=regimes.get(event.event_date),
                )
            )
    return out


def stratify_by_ticker(
    event_returns: Sequence[EventReturns],
    *,
    windows: Sequence[tuple[str, int, int]] = DEFAULT_WINDOWS,
) -> list[StratumStats]:
    by_ticker: dict[str, list[EventReturns]] = {}
    for er in event_returns:
        by_ticker.setdefault(er.event.ticker, []).append(er)
    return [
        aggregate(events, stratum_key=ticker, windows=windows)
        for ticker, events in sorted(by_ticker.items())
    ]


def stratify_by_item_code(
    event_returns: Sequence[EventReturns],
    *,
    windows: Sequence[tuple[str, int, int]] = DEFAULT_WINDOWS,
) -> list[StratumStats]:
    """Group events by individual item code (an event with items=2.02,9.01
    contributes to BOTH the 2.02 and 9.01 buckets).

    Comma-list 8-Ks are common — Item 9.01 (Exhibits) almost always
    rides with Item 2.02 (earnings) or 5.02 (officer change). Counting
    each item independently gives an honest "what does Item X tend to
    do" answer without the confounding from the co-filed items.
    """
    by_item: dict[str, list[EventReturns]] = {}
    for er in event_returns:
        for code in er.event.item_codes:
            by_item.setdefault(code, []).append(er)
    return [
        aggregate(events, stratum_key=code, windows=windows)
        for code, events in sorted(by_item.items())
    ]


def stratify_by_regime(
    event_returns: Sequence[EventReturns],
    *,
    windows: Sequence[tuple[str, int, int]] = DEFAULT_WINDOWS,
) -> list[StratumStats]:
    by_regime: dict[str, list[EventReturns]] = {}
    for er in event_returns:
        key = er.regime or "unknown"
        by_regime.setdefault(key, []).append(er)
    return [
        aggregate(events, stratum_key=regime, windows=windows)
        for regime, events in sorted(by_regime.items())
    ]
