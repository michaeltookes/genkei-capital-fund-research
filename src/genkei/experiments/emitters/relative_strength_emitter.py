"""Relative-strength → signal_events emitter (B-098).

Fifth emitter wired into the cross-source correlator (B-064), and the
**second crypto-side source** — the pair that completes the engine's
ability to fire crypto stacks. With B-095 (TVL drawdown) and B-098 both
live, the pre-staged ``crypto_tvl_stress_combo`` rule's
``min_distinct_sources ≥ 2`` gate is finally crossable. Same inflection
B-093 played for equity-core after B-064.

**What the signal captures.** "Asset is dramatically out- or under-
performing the crypto market over the trailing 30 days." BTC is the
fixed peer benchmark (the crypto-market analog of SPY for equity-side
rules) so every watchlist crypto asset's signal answers the same
question: "is this name leading or lagging BTC?" Asset-vs-asset
relative strength is what surfaces names that move *with* a regime
change (BTC also fell) vs *against* it (asset-specific weakness even
while BTC held up). The latter is the more informative read for
stack-forming because it's the asset-specific component, not the market
beta.

**Episode model.** Same as tvl_drawdown — emit ONE event per crossing
*onset* (first day rel-strength crosses past the threshold) and skip
continued-out-of-band days. A long underperformance run produces one
``laggard_crossing`` event, not 60. The state machine has three states
per asset (laggard, neutral, leader); transitions into laggard / leader
emit; transitions back into neutral do not (the "stress lifted" signal
is implicit in the next leader_crossing in the opposite direction).

**Thresholds.** ``LAGGARD_THRESHOLD_PCT = -15`` and
``LEADER_THRESHOLD_PCT = +15`` — asset 15pp behind / ahead of BTC over
30 days. Crypto routinely sees ±20%+ absolute moves, so the rel-strength
threshold needs to be wide enough that minor noise doesn't fire daily
crossings while still being narrow enough that real episodes register.
15pp / 30 days ≈ one major macro / asset-specific event per quarter.
Tunable; constants live in this module so a re-tune shows up in git
blame here (matching the precedent of the prior emitters).

**Strength.** ``min(abs(rel_strength_pct) / STRENGTH_SATURATION_PP, 1.0)``.
With the current 20pp saturation point, ±15pp at threshold edge → 0.75 and
±20pp at saturation → 1.0.

Field mapping per event:

* ``asset``         = watchlist CoinGecko ID (ethereum / solana / chainlink / ...),
                      matching the crypto identifiers used by tvl_drawdown.
* ``asset_class``   = ``"crypto"``.
* ``ts``            = the crossing onset date at UTC midnight.
* ``source``        = ``"relative_strength"`` (matches the source name
                      in ``signal_rules.yml``'s ``crypto_tvl_stress_combo``).
* ``signal_kind``   = ``"laggard_crossing"`` (bearish) or
                      ``"leader_crossing"`` (bullish).
* ``direction``     = ``"bearish"`` for laggard, ``"bullish"`` for leader.
* ``strength``      = saturating-ramp on ``abs(rel_strength_pct)``.
* ``horizon``       = ``"crypto:{sleeve}"`` per the asset's watchlist
                      sleeve (ETH/SOL/LINK at ``crypto:core``, SUI at
                      ``crypto:tactical``).
* ``source_ref``    = ``"<asset>:BTC:30d:<crossing_iso>"`` — natural
                      key of the crossing. UNIQUE constraint on
                      ``(asset, ts, source, signal_kind, source_ref, horizon)``
                      makes re-emission idempotent.

Loading note: loads the full available price series for each (asset, BTC)
pair regardless of ``--since``. Assets use ``coinbase.candles`` when a
live ``coinbase_product`` is configured and fall back to
``coingecko.market_data`` otherwise; BTC remains the fixed Coinbase peer.
The relative-strength window needs ≥30 days of trailing history and the
crossing detector needs the prior day's state. ``--since`` is applied
in-Python to the *emission* window after crossings are detected.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, Watchlist, load_watchlist
from genkei.experiments.relative_strength import PricePoint, compute_return_pct
from genkei.experiments.signal_store import emit_signals_bulk

EMITTER_SOURCE = "relative_strength"
EMITTER_RUN_TAG = "signal_emitter"
EMITTER_ENDPOINT = "relative_strength"

# Fixed crypto-market benchmark. BTC is the natural peer for every
# watchlist crypto asset — the crypto-side analog of SPY for equities.
# Stored as the coinbase product code so it joins coinbase.candles
# directly.
PEER_PRODUCT = "BTC-USD"
PEER_SYMBOL = "BTC"

# Trailing-return window. 30d matches the `crypto_tvl_stress_combo`
# rule's window_days in signal_rules.yml — a `laggard_crossing` is
# meaningful at the same horizon the rule operates over.
WINDOW_DAYS = 30

# Bearish / bullish threshold edges. ±15pp over 30d is selective enough
# that minor noise doesn't fire crossings daily but wide enough that
# real macro / asset-specific episodes register.
LAGGARD_THRESHOLD_PCT = Decimal("-15")
LEADER_THRESHOLD_PCT = Decimal("15")

# Saturation magnitude. abs(rel_strength_pct) / 20 clamped to [0, 1] —
# ±15pp at threshold edge → 0.75; ±20pp at saturation → 1.0. Picked
# from the live 2017-present coinbase data: real ETH-vs-BTC stress
# episodes cluster in the 15-25pp magnitude range, so saturating at
# 20pp gives a meaningful strength to threshold-edge crossings rather
# than the more conservative 30pp range that would leave a -17pp
# laggard reading with strength 0.58 (too low to combine with a
# coincident TVL stress event into a fireable stack score). The
# saturation point lives in this module so a re-tune shows up in git
# blame here, matching the precedent of the prior emitters.
STRENGTH_SATURATION_PP = Decimal("20")

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


@dataclass(frozen=True)
class _CryptoAssetTarget:
    """One watchlist crypto asset plus the table/key to load its prices from."""

    asset_id: str
    symbol: str
    price_source: str  # "coinbase" | "coingecko"
    price_key: str
    sleeve: str


def _state_for(rel_strength_pct: Decimal | None) -> str | None:
    """Classify a rel-strength value into ``"laggard" | "neutral" | "leader"``
    or ``None`` when input is None.

    Pure helper, used by the crossing detector. Threshold semantics:
    rel_strength_pct ≤ LAGGARD_THRESHOLD_PCT → laggard,
    rel_strength_pct ≥ LEADER_THRESHOLD_PCT → leader, else neutral.
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

    Uses STRENGTH_SATURATION_PP: ±15pp → 0.75, ±20pp → 1.0.
    """
    magnitude = abs(rel_strength_pct)
    scaled = magnitude / STRENGTH_SATURATION_PP
    return Decimal("1") if scaled > Decimal("1") else scaled


def _date_ts(crossing_date: date) -> datetime:
    """Convert a crossing date to a UTC midnight datetime."""
    return datetime.combine(crossing_date, time(0, 0, tzinfo=timezone.utc))


def _load_price_series(product: str, *, until: date | None = None) -> list[PricePoint]:
    """Pull (ts, close) rows from ``coinbase.candles`` for one product.

    Returns rows ascending by date. Inner join on a date appears only
    when ``close`` is non-null. Pre-listing days are silently absent.
    The series is full-history (no ``since`` bound) because the caller
    needs trailing lookback for the relative-strength window AND the
    prior day's state for the crossing detector.
    """
    sql = (
        "SELECT ts::date AS d, close::numeric "
        "FROM coinbase.candles "
        "WHERE product = %s AND close IS NOT NULL"
    )
    params: list[Any] = [product]
    if until is not None:
        sql += " AND ts::date <= %s"
        params.append(until)
    sql += " ORDER BY ts::date ASC"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [PricePoint(ts=d, price_usd=Decimal(price)) for d, price in rows]


def _load_coingecko_price_series(
    coingecko_id: str, *, until: date | None = None
) -> list[PricePoint]:
    """Pull (ts, price_usd) rows from ``coingecko.market_data`` for one asset."""
    sql = (
        "SELECT ts::date AS d, price_usd::numeric "
        "FROM coingecko.market_data "
        "WHERE coingecko_id = %s AND price_usd IS NOT NULL"
    )
    params: list[Any] = [coingecko_id]
    if until is not None:
        sql += " AND ts::date <= %s"
        params.append(until)
    sql += " ORDER BY ts::date ASC"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [PricePoint(ts=d, price_usd=Decimal(price)) for d, price in rows]


def _load_asset_price_series(
    target: _CryptoAssetTarget, *, until: date | None = None
) -> list[PricePoint]:
    """Load one asset's price history from its configured watchlist source."""
    if target.price_source == "coinbase":
        return _load_price_series(target.price_key, until=until)
    if target.price_source == "coingecko":
        return _load_coingecko_price_series(target.price_key, until=until)
    raise ValueError(f"unsupported crypto price source: {target.price_source}")


def compute_daily_relative_strength(
    asset_series: Sequence[PricePoint],
    peer_series: Sequence[PricePoint],
    *,
    window_days: int = WINDOW_DAYS,
) -> list[tuple[date, Decimal, Decimal, Decimal]]:
    """For each day where BOTH series have a ``window_days`` lookback,
    return ``(date, asset_return_pct, peer_return_pct, rel_strength_pct)``.

    The asset side anchors the date; the peer is looked up via
    ``compute_return_pct`` over its own series (so a missing peer day
    falls back to the most-recent prior peer observation, mirroring the
    Postgres view's lookback semantics from B-090).
    """
    if not asset_series or not peer_series:
        return []
    peer_by_date: dict[date, PricePoint] = {p.ts: p for p in peer_series}
    sorted_asset = sorted(asset_series, key=lambda p: p.ts)
    out: list[tuple[date, Decimal, Decimal, Decimal]] = []
    for i, anchor in enumerate(sorted_asset):
        if anchor.ts not in peer_by_date:
            # Peer didn't trade on this exact date — skip rather than
            # invent a lookback peer price (the asset's anchor is the
            # truth source; missing peer days are typically very early
            # in the asset's history where peer data is also sparse).
            continue
        asset_return, _, _ = compute_return_pct(
            list(sorted_asset[: i + 1]), window_days=window_days
        )
        # Cut the peer series at the same anchor date so its compute_return_pct
        # uses the same trailing-window framing the asset does.
        peer_through_anchor = [p for p in peer_series if p.ts <= anchor.ts]
        peer_return, _, _ = compute_return_pct(
            peer_through_anchor, window_days=window_days
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
    episode-onset semantics as the tvl_drawdown emitter.
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
        "asset_class": "crypto",
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


def _crypto_assets(watchlist: Watchlist) -> list[_CryptoAssetTarget]:
    """Return signal-scoped crypto assets, excluding the fixed BTC peer.

    Coinbase remains preferred when a live product is configured because it
    matches the emitter's original history. CoinGecko keeps primary assets
    with delisted or unavailable Coinbase products in the signal feed.
    """
    out: list[_CryptoAssetTarget] = []
    for entry in watchlist.crypto:
        product = entry.coinbase_product.strip() if entry.coinbase_product else ""
        asset_id = entry.coingecko_id.strip()
        symbol = entry.symbol.upper()
        if not asset_id:
            continue
        if product == PEER_PRODUCT or symbol == PEER_SYMBOL or asset_id == "bitcoin":
            continue
        if product:
            price_source = "coinbase"
            price_key = product
        else:
            price_source = "coingecko"
            price_key = asset_id
        out.append(
            _CryptoAssetTarget(
                asset_id=asset_id,
                symbol=entry.symbol,
                price_source=price_source,
                price_key=price_key,
                sleeve=entry.sleeve or "core",
            )
        )
    return out


def emit_recent_crossings(
    *,
    since: date | None = None,
    until: date | None = None,
    config: Path = DEFAULT_WATCHLIST_PATH,
) -> EmitResult:
    """Detect relative-strength crossings per crypto asset and emit signal
    events.

    Loads the full available coinbase.candles series for each (asset,
    BTC) pair regardless of ``--since`` (the rel-strength window needs
    ≥30 days of trailing history and the crossing detector needs prior
    state). ``--since`` is applied in-Python to the *emission* window
    after crossings are detected.

    Wrapped in a single ``meta.ingest_runs`` row so the emitter is
    queryable via ``genkei watchlist health`` like any other source.
    """
    watchlist = load_watchlist(config)
    assets = _crypto_assets(watchlist)

    with db.ingest_run(
        EMITTER_RUN_TAG,
        endpoint=EMITTER_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "peer": PEER_SYMBOL,
            "window_days": WINDOW_DAYS,
            "asset_count": len(assets),
        },
    ) as run:
        if not assets:
            LOGGER.warning(
                "no watchlist crypto entries carry a coingecko_id (other than "
                "BTC); relative-strength emitter has nothing to scope to"
            )
            return EmitResult(
                ingest_run_id=run.id,
                crossings_emitted=0,
                assets_skipped_no_watchlist=0,
                assets_skipped_no_data=0,
            )

        peer_series = _load_price_series(PEER_PRODUCT, until=until)
        if not peer_series:
            LOGGER.warning(
                "no %s price data; relative-strength emitter cannot compute "
                "any rel-strength values",
                PEER_PRODUCT,
            )
            return EmitResult(
                ingest_run_id=run.id,
                crossings_emitted=0,
                assets_skipped_no_watchlist=0,
                assets_skipped_no_data=len(assets),
            )

        events: list[dict[str, Any]] = []
        assets_skipped_no_data = 0
        for asset in assets:
            asset_series = _load_asset_price_series(asset, until=until)
            if not asset_series:
                LOGGER.warning(
                    "no %s price data for %s; skipping relative-strength emission",
                    asset.price_source,
                    asset.price_key,
                )
                assets_skipped_no_data += 1
                continue
            horizon = f"crypto:{asset.sleeve}"
            daily = compute_daily_relative_strength(
                asset_series, peer_series, window_days=WINDOW_DAYS
            )
            crossings = _detect_crossings(daily, asset=asset.asset_id)
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
    import argparse

    def parse_date_arg(label: str) -> Any:
        def parse(raw: str) -> date | None:
            try:
                return _parse_date(raw, label=label)
            except Exception as exc:
                raise argparse.ArgumentTypeError(str(exc)) from exc

        return parse

    parser = argparse.ArgumentParser(
        description="Emit relative-strength crossing events into meta.signal_events."
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
            f"relative-strength emitter wrote ingest_run_id={result.ingest_run_id} "
            f"crossings={result.crossings_emitted} "
            f"assets_skipped_no_data={result.assets_skipped_no_data}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
