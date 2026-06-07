"""Equity rel-strength → signal_events emitter (B-111).

Equity-side counterpart to B-098's crypto rel-strength emitter. The
template is established; this module generalizes it to read from
``yahoo.candles`` (instead of ``coinbase.candles``) and to use SPY as
the fixed peer benchmark (instead of BTC). Everything else — the
saturating-ramp strength helper, the three-state machine, the
episode-onset detector, the idempotent ``source_ref`` shape, the
single ``meta.ingest_runs`` wrapping — is the same shape as B-098 so
the cross-source correlator (B-064) consumes equity and crypto
rel-strength events uniformly.

**What the signal captures.** "Asset is dramatically out- or under-
performing the broad US equity market over the trailing 30 calendar
days." SPY is the fixed peer (the equity-core sleeve baseline from
B-102) so every watchlist equity's signal answers the same question:
"is this name leading or lagging the index?" Asset-vs-index relative
strength is the textbook way to surface names that move *with* a
regime change vs *against* it — the latter being the more informative
read for stack-forming because it's the asset-specific component, not
the market beta.

**Episode model.** Same as B-095 (TVL drawdown) and B-098 (crypto
rel-strength) — emit ONE event per crossing *onset* (first day
rel-strength crosses past the threshold) and skip continued-out-of-
band days. A long underperformance run produces one
``laggard_crossing`` event, not 60. The state machine has three states
per asset (laggard, neutral, leader); transitions into laggard / leader
emit; transitions back into neutral do not (the "stress lifted" signal
is implicit in the next leader_crossing in the opposite direction).

**Thresholds.** ``LAGGARD_THRESHOLD_PCT = -10`` and
``LEADER_THRESHOLD_PCT = +10`` — asset 10pp behind / ahead of SPY
over 30 days. **2/3 of B-098's crypto values** because equity
volatility is ~2/3 of crypto over comparable windows; -15pp at 30d
on an equity name is a rare, extreme event whereas crypto routinely
sees ±20%+. Tunable; constants live in this module so a re-tune
shows up in git blame here (matching B-098's precedent).

**Strength.** ``min(abs(rel_strength_pct) / STRENGTH_SATURATION_PP, 1.0)``.
With the current 15pp saturation point, ±10pp at threshold edge → 0.67
and ±15pp at saturation → 1.0. Picked from the 2026-06-05 SaaS-sector
research session's observed 30d magnitudes (SaaS pure-plays vs SPY
clustered around -8 to -15pp during the active drawdown months); the
threshold + saturation pair gives meaningful strength to threshold-
edge crossings rather than requiring extreme magnitudes.

Field mapping per event:

* ``asset``         = watchlist equity symbol (CRM / NOW / AAPL / ...).
* ``asset_class``   = ``"equity"``.
* ``ts``            = the crossing onset date at UTC midnight.
* ``source``        = ``"equity_relative_strength"`` (distinct from the
                      crypto-side ``"relative_strength"`` so
                      ``signal_rules.yml`` can target each independently).
* ``signal_kind``   = ``"laggard_crossing"`` (bearish) or
                      ``"leader_crossing"`` (bullish).
* ``direction``     = ``"bearish"`` for laggard, ``"bullish"`` for leader.
* ``strength``      = saturating-ramp on ``abs(rel_strength_pct)``.
* ``horizon``       = ``"equity:{sleeve}"`` per the asset's watchlist
                      sleeve (today's watchlist is uniformly core; a
                      future tactical-sleeve equity is routed
                      automatically).
* ``source_ref``    = ``"<ticker>:SPY:30d:<crossing_iso>"`` — natural
                      key of the crossing. UNIQUE constraint on
                      ``(asset, ts, source, signal_kind, source_ref,
                      horizon)`` makes re-emission idempotent.

Loading note: loads the full available ``yahoo.candles`` series for
each (asset, SPY) pair regardless of ``--since``. The relative-
strength window needs ≥30 calendar days of trailing history and the
crossing detector needs the prior day's state. ``--since`` is applied
in-Python to the *emission* window after crossings are detected.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, Watchlist, load_watchlist
from genkei.experiments.relative_strength import PricePoint
from genkei.experiments.signal_store import emit_signals_bulk

EMITTER_SOURCE = "equity_relative_strength"
EMITTER_RUN_TAG = "signal_emitter"
EMITTER_ENDPOINT = "equity_relative_strength"

# Equity-market benchmark tickers. Per B-112's sector-routed peer
# selection, each watchlist equity routes to EXACTLY ONE peer based on
# its ``sector`` field — tech-comp sectors → QQQ, everything else →
# SPY. The previous B-111 design used SPY as the single fixed peer
# for every watchlist equity; B-112 generalizes to dual peers so the
# tech sub-cohort (semis, software, cloud, data, fintech, crypto-equity)
# is compared against its natural tech benchmark rather than the
# broad-market index.
SPY_TICKER = "SPY"
SPY_SYMBOL = "SPY"
QQQ_TICKER = "QQQ"
QQQ_SYMBOL = "QQQ"
# Back-compat aliases for callers that imported the singular form pre-B-112.
# Defaults to SPY because that's the broad-market baseline; downstream
# tests / consumers that don't pass a peer explicitly continue to behave
# as they did under B-111.
PEER_TICKER = SPY_TICKER
PEER_SYMBOL = SPY_SYMBOL

# Sector-keyword routing (B-112). Each watchlist equity entry carries
# a free-text ``sector`` field (e.g. ``"Enterprise software"``,
# ``"Semiconductors / foundry"``, ``"Banking"``); the emitter routes
# to QQQ when ANY of these substrings appears in the lowercased sector
# string. The list is calibrated against the current watchlist's
# sector strings — covers semis, software, cloud, internet, data,
# server, fintech, crypto, bitcoin, and EV. Future watchlist additions
# that introduce a new tech-comp sector keyword need to extend this
# list. Constants live in this module so a re-tune shows up in git
# blame here (matching B-098 / B-111 precedent).
QQQ_SECTOR_KEYWORDS: tuple[str, ...] = (
    "technology",      # AAPL ("Consumer technology")
    "software",        # MSFT / CRM / NOW / ADBE / WDAY / AVGO / PLTR
    "semiconductor",   # NVDA / AMD / AVGO / MU / TSM
    "internet",        # GOOG / GOOGL / META
    "cloud",           # MSFT / AMZN / GOOG / DOCN
    "data",            # SNOW / PLTR
    "server",          # SMCI
    "fintech",         # SOFI / HOOD
    "crypto",          # COIN
    "bitcoin",         # MSTR / MARA / RIOT (treasury + mining)
    "ev /",            # TSLA ("EV / energy"); deliberately includes the
                       # space-slash to avoid false positives on words
                       # like "level" or "revenue"
)

# Trailing-return window. 30 calendar days matches B-098's crypto
# emitter — same horizon, same semantic. The return helper uses close-
# on-or-before lookback so weekends / holidays / sparse early data are
# handled gracefully.
WINDOW_DAYS = 30

# Bearish / bullish threshold edges. ±10pp over 30d is the equity
# analog of B-098's ±15pp for crypto — 2/3 of the crypto value because
# equity volatility is ~2/3 of crypto over comparable windows.
# Selective enough that minor noise doesn't fire crossings daily but
# wide enough that real macro / asset-specific episodes register.
LAGGARD_THRESHOLD_PCT = Decimal("-10")
LEADER_THRESHOLD_PCT = Decimal("10")

# Saturation magnitude. ``abs(rel_strength_pct) / 15`` clamped to
# [0, 1] — ±10pp at threshold edge → 0.67; ±15pp at saturation → 1.0.
# Picked from the 2026-06-05 SaaS-sector research session's observed
# 30d magnitudes (SaaS pure-plays vs SPY clustered around -8 to -15pp
# during the active drawdown months); saturating at 15pp gives a
# meaningful strength to threshold-edge crossings rather than the
# more conservative 20pp range that would leave a -12pp laggard
# reading with strength 0.6 (too low to combine with a coincident
# insider sell cluster into a fireable stack score).
STRENGTH_SATURATION_PP = Decimal("15")

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Crossing:
    """One detected crossing onset for an (asset, peer, window) tuple."""

    asset: str
    peer: str
    ts: date
    kind: str  # "laggard_crossing" | "leader_crossing"
    rel_strength_pct: Decimal
    asset_return_pct: Decimal
    peer_return_pct: Decimal


@dataclass(frozen=True)
class EmitResult:
    """Return value of ``emit_recent_crossings`` for CLI / test inspection."""

    ingest_run_id: int
    crossings_emitted: int
    assets_skipped_no_watchlist: int
    assets_skipped_no_data: int


def _state_for(rel_strength_pct: Decimal | None) -> str | None:
    """Classify a rel-strength value into ``"laggard" | "neutral" | "leader"``
    or ``None`` when input is None.

    Pure helper, used by the crossing detector. Threshold semantics:
    ``rel_strength_pct <= LAGGARD_THRESHOLD_PCT`` → laggard,
    ``rel_strength_pct >= LEADER_THRESHOLD_PCT`` → leader, else neutral.
    """
    if rel_strength_pct is None:
        return None
    if rel_strength_pct <= LAGGARD_THRESHOLD_PCT:
        return "laggard"
    if rel_strength_pct >= LEADER_THRESHOLD_PCT:
        return "leader"
    return "neutral"


def _strength_from_rel_strength(rel_strength_pct: Decimal) -> Decimal:
    """Saturating-ramp on ``abs(rel_strength_pct)``.

    Uses STRENGTH_SATURATION_PP: ±10pp → 0.67, ±15pp → 1.0.
    """
    magnitude = abs(rel_strength_pct)
    scaled = magnitude / STRENGTH_SATURATION_PP
    return Decimal("1") if scaled > Decimal("1") else scaled


def _date_ts(crossing_date: date) -> datetime:
    """Convert a crossing date to a UTC midnight datetime."""
    return datetime.combine(crossing_date, time(0, 0, tzinfo=timezone.utc))


def _load_price_series(ticker: str, *, until: date | None = None) -> list[PricePoint]:
    """Pull adjusted (ts, price) rows from ``yahoo.candles`` for one ticker.

    Returns rows ascending by date, preferring split/dividend-adjusted
    ``adj_close`` and falling back to raw ``close`` when adjusted data
    is absent. Pre-listing days are silently absent. Full-history load
    (no ``since`` bound) because the caller needs trailing lookback for
    the relative-strength window AND the prior day's state for the
    crossing detector.
    """
    sql = (
        "SELECT ts::date AS d, COALESCE(adj_close, close)::numeric "
        "FROM yahoo.candles "
        "WHERE ticker = %s AND COALESCE(adj_close, close) IS NOT NULL"
    )
    params: list[Any] = [ticker]
    if until is not None:
        sql += " AND ts::date <= %s"
        params.append(until)
    sql += " ORDER BY ts::date ASC"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [PricePoint(ts=d, price_usd=Decimal(price)) for d, price in rows]


def _compute_return_pct_through(
    sorted_series: Sequence[PricePoint],
    dates: Sequence[date],
    end_index: int,
    *,
    window_days: int,
) -> Decimal | None:
    """Compute return using ``sorted_series[:end_index]`` without slicing."""
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    if end_index < 1:
        return None
    latest = sorted_series[end_index - 1]
    target_ts = latest.ts - timedelta(days=window_days)
    lookback_end = bisect_right(dates, target_ts, hi=end_index - 1)
    if lookback_end == 0:
        return None
    lookback = sorted_series[lookback_end - 1]
    if lookback.price_usd == 0:
        return None
    return (latest.price_usd - lookback.price_usd) / lookback.price_usd * Decimal(100)


def compute_daily_relative_strength(
    asset_series: Sequence[PricePoint],
    peer_series: Sequence[PricePoint],
    *,
    window_days: int = WINDOW_DAYS,
) -> list[tuple[date, Decimal, Decimal, Decimal]]:
    """For each day where BOTH series have a ``window_days`` lookback,
    return ``(date, asset_return_pct, peer_return_pct, rel_strength_pct)``.

    The asset side anchors the date; the peer is looked up by exact
    date match. Equities trade the NYSE calendar so both the asset
    and SPY have observations on the same trading days — non-trading
    days are simply absent from both series.
    """
    if not asset_series or not peer_series:
        return []
    sorted_asset = sorted(asset_series, key=lambda p: p.ts)
    sorted_peer = sorted(peer_series, key=lambda p: p.ts)
    asset_dates = [point.ts for point in sorted_asset]
    peer_dates = [point.ts for point in sorted_peer]
    peer_date_set = set(peer_dates)
    out: list[tuple[date, Decimal, Decimal, Decimal]] = []
    for i, anchor in enumerate(sorted_asset):
        if anchor.ts not in peer_date_set:
            # Peer didn't trade on this exact date — skip rather than
            # invent a lookback peer price. For equities this should be
            # essentially never (both target and SPY share the NYSE
            # calendar) but the defensive skip matches B-098.
            continue
        asset_return = _compute_return_pct_through(
            sorted_asset, asset_dates, i + 1, window_days=window_days
        )
        peer_end = bisect_right(peer_dates, anchor.ts)
        peer_return = _compute_return_pct_through(
            sorted_peer, peer_dates, peer_end, window_days=window_days
        )
        if asset_return is None or peer_return is None:
            continue
        rel_strength = asset_return - peer_return
        out.append((anchor.ts, asset_return, peer_return, rel_strength))
    return out


def _detect_crossings(
    daily: Sequence[tuple[date, Decimal, Decimal, Decimal]],
    *,
    asset: str,
    peer: str = PEER_SYMBOL,
) -> list[Crossing]:
    """Walk daily rel-strength rows, return one Crossing per state-onset.

    Tracks the prior day's state (laggard / neutral / leader). A
    transition INTO laggard or leader emits; transitions back into
    neutral are silent (the "stress lifted" implicit signal). Same
    episode-onset semantics as B-095 and B-098.
    """
    crossings: list[Crossing] = []
    prev_state: str | None = None
    for ts, asset_ret, peer_ret, rel in daily:
        state = _state_for(rel)
        if state is None:
            continue
        if state != prev_state and state in ("laggard", "leader"):
            crossings.append(
                Crossing(
                    asset=asset,
                    peer=peer,
                    ts=ts,
                    kind=f"{state}_crossing",
                    rel_strength_pct=rel,
                    asset_return_pct=asset_ret,
                    peer_return_pct=peer_ret,
                )
            )
        prev_state = state
    return crossings


def _build_event(
    crossing: Crossing,
    *,
    horizon: str,
) -> dict[str, Any]:
    """Build one signal event row from a crossing onset."""
    ts = _date_ts(crossing.ts)
    strength = _strength_from_rel_strength(crossing.rel_strength_pct)
    direction = "bearish" if crossing.kind == "laggard_crossing" else "bullish"
    payload: dict[str, Any] = {
        "asset": crossing.asset,
        "peer": crossing.peer,
        "horizon": horizon,
        "window_days": WINDOW_DAYS,
        "crossing_date": crossing.ts.isoformat(),
        "rel_strength_pct": str(crossing.rel_strength_pct),
        "asset_return_pct": str(crossing.asset_return_pct),
        "peer_return_pct": str(crossing.peer_return_pct),
        "thresholds": {
            "laggard_pct": str(LAGGARD_THRESHOLD_PCT),
            "leader_pct": str(LEADER_THRESHOLD_PCT),
        },
    }
    return {
        "asset": crossing.asset,
        "asset_class": "equity",
        "horizon": horizon,
        "ts": ts,
        "source": EMITTER_SOURCE,
        "signal_kind": crossing.kind,
        "direction": direction,
        "strength": strength,
        "payload": payload,
        "source_ref": (
            f"{crossing.asset}:{crossing.peer}:{WINDOW_DAYS}d:{crossing.ts.isoformat()}"
        ),
    }


def _peer_for_sector(sector: str | None) -> str:
    """Sector-route an equity to its rel-strength peer (B-112).

    Returns ``"QQQ"`` when the lowercased ``sector`` string contains any
    of the keywords in :data:`QQQ_SECTOR_KEYWORDS` (tech / semis /
    software / cloud / data / server / fintech / crypto / bitcoin / EV);
    otherwise returns ``"SPY"``. Missing or empty sector strings default
    to SPY — the broad-market baseline is the safe fallback when the
    classification is unknown.
    """
    if not sector:
        return SPY_TICKER
    lower = sector.lower()
    if any(keyword in lower for keyword in QQQ_SECTOR_KEYWORDS):
        return QQQ_TICKER
    return SPY_TICKER


def _equity_assets(watchlist: Watchlist) -> list[tuple[str, str, str]]:
    """Return ``[(ticker, sleeve, peer_ticker)]`` for watchlist equities.

    Iterates ``watchlist.equities`` (the equity research-targets section)
    — benchmarks (SPY / QQQ / IWM) and ETFs (the spot crypto ETFs from
    B-105's etf_tickers) are intentionally NOT included; they're
    comparators / activity-tracking targets, not research subjects the
    engine should fire rel-strength stacks on. Per B-112, each entry's
    peer (SPY or QQQ) is determined by sector-keyword routing via
    :func:`_peer_for_sector`. Equities whose ticker matches either peer
    (SPY or QQQ themselves) are excluded so the emitter doesn't compute
    peer-vs-peer.
    """
    excluded = {SPY_TICKER, QQQ_TICKER}
    out: list[tuple[str, str, str]] = []
    for entry in watchlist.equities:
        ticker = entry.symbol.strip().upper()
        if not ticker or ticker in excluded:
            continue
        peer = _peer_for_sector(entry.sector)
        out.append((ticker, entry.sleeve or "core", peer))
    return out


def emit_recent_crossings(
    *,
    since: date | None = None,
    until: date | None = None,
    config: Path = DEFAULT_WATCHLIST_PATH,
) -> EmitResult:
    """Detect relative-strength crossings per equity asset and emit signal
    events.

    Per B-112, each watchlist equity routes to EXACTLY ONE peer
    (SPY or QQQ) based on its ``sector`` field via
    :func:`_peer_for_sector`. The emitter pre-loads both peer series
    once at the top, then per-asset uses the right peer for the
    rel-strength math.

    Loads the full available yahoo.candles series for each (asset, peer)
    pair regardless of ``--since`` (the rel-strength window needs ≥30
    calendar days of trailing history and the crossing detector needs
    prior state). ``--since`` is applied in-Python to the *emission*
    window after crossings are detected.

    Wrapped in a single ``meta.ingest_runs`` row so the emitter is
    queryable via ``genkei watchlist health`` like any other source.
    """
    watchlist = load_watchlist(config)
    assets = _equity_assets(watchlist)
    peers_used = {peer for _, _, peer in assets}

    with db.ingest_run(
        EMITTER_RUN_TAG,
        endpoint=EMITTER_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "peers": sorted(peers_used),
            "window_days": WINDOW_DAYS,
            "asset_count": len(assets),
        },
    ) as run:
        if not assets:
            LOGGER.warning(
                "no watchlist equities (other than %s / %s); equity "
                "rel-strength emitter has nothing to scope to",
                SPY_TICKER,
                QQQ_TICKER,
            )
            return EmitResult(
                ingest_run_id=run.id,
                crossings_emitted=0,
                assets_skipped_no_watchlist=0,
                assets_skipped_no_data=0,
            )

        # Pre-load every needed peer series once. For the typical
        # watchlist this is 2 queries (SPY + QQQ). If a future
        # _peer_for_sector adds more peers (IWM for small-cap?), they
        # land here automatically without changes to the inner loop.
        peer_series_by_ticker: dict[str, list[PricePoint]] = {}
        for peer_ticker in peers_used:
            series = _load_price_series(peer_ticker, until=until)
            if not series:
                LOGGER.warning(
                    "no %s price data; equity rel-strength emitter cannot "
                    "compute rel-strength values for %s-routed assets",
                    peer_ticker,
                    peer_ticker,
                )
                continue
            peer_series_by_ticker[peer_ticker] = series

        if not peer_series_by_ticker:
            return EmitResult(
                ingest_run_id=run.id,
                crossings_emitted=0,
                assets_skipped_no_watchlist=0,
                assets_skipped_no_data=len(assets),
            )

        events: list[dict[str, Any]] = []
        assets_skipped_no_data = 0
        for ticker, sleeve, peer_ticker in assets:
            peer_series = peer_series_by_ticker.get(peer_ticker)
            if peer_series is None:
                # The assigned peer had no data; skip this asset
                # (logged once above per missing peer).
                assets_skipped_no_data += 1
                continue
            asset_series = _load_price_series(ticker, until=until)
            if not asset_series:
                LOGGER.warning(
                    "no yahoo.candles data for %s; skipping rel-strength "
                    "emission",
                    ticker,
                )
                assets_skipped_no_data += 1
                continue
            horizon = f"equity:{sleeve}"
            daily = compute_daily_relative_strength(
                asset_series, peer_series, window_days=WINDOW_DAYS
            )
            # Use the asset's assigned peer in the Crossing constructor
            # so source_ref naturally carries the peer code; events from
            # SPY-routed and QQQ-routed assets land as distinct natural
            # keys even if the same ticker were routed to both in a
            # future v3 dual-peer extension.
            crossings = _detect_crossings(daily, asset=ticker, peer=peer_ticker)
            for crossing in crossings:
                if since is not None and crossing.ts < since:
                    continue
                if until is not None and crossing.ts > until:
                    continue
                events.append(_build_event(crossing, horizon=horizon))

        rows_written = emit_signals_bulk(events, ingest_run_id=run.id)
        run.add_rows(rows_written)
        return EmitResult(
            ingest_run_id=run.id,
            crossings_emitted=rows_written,
            assets_skipped_no_watchlist=0,
            assets_skipped_no_data=assets_skipped_no_data,
        )


def parse_args(argv: list[str]) -> Any:
    """Parse CLI args for the equity relative-strength emitter."""
    import argparse

    def parse_date_arg(label: str) -> Any:
        """Return an argparse type function for one date-labeled flag."""
        def parse(raw: str) -> date | None:
            """Parse one date argument and convert errors for argparse."""
            try:
                return _parse_date(raw, label=label)
            except Exception as exc:
                raise argparse.ArgumentTypeError(str(exc)) from exc

        return parse

    parser = argparse.ArgumentParser(
        description="Emit equity rel-strength crossing events into meta.signal_events."
    )
    parser.add_argument("--since", type=parse_date_arg("since"), default=None)
    parser.add_argument("--until", type=parse_date_arg("until"), default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the emitter CLI and print either JSON or a concise summary."""
    import json as _json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    result = emit_recent_crossings(since=args.since, until=args.until)
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "crossings_emitted": result.crossings_emitted,
                    "assets_skipped_no_watchlist": result.assets_skipped_no_watchlist,
                    "assets_skipped_no_data": result.assets_skipped_no_data,
                    "source": EMITTER_SOURCE,
                },
                default=_json_default,
            )
        )
    else:
        print(
            f"equity rel-strength emitter wrote ingest_run_id={result.ingest_run_id} "
            f"crossings={result.crossings_emitted} "
            f"assets_skipped_no_data={result.assets_skipped_no_data}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
