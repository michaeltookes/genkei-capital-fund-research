"""TVL drawdown → signal_events emitter (B-095).

Fourth emitter wired into the cross-source correlator (B-064), after the
three equity-side emitters (insider_clusters / crowding / eight_k_impact).
**This is the engine's first crypto-side signal source.** It adapts B-058's
rule-based TVL-stress classifier into atomic signal events keyed to each
chain's native token (Ethereum → ETH, Solana → SOL, Sui → SUI). Pairs
naturally with the follow-up ``relative_strength_emitter`` (B-098) — the
two together will satisfy the correlator's ``min_distinct_sources ≥ 2``
gate for crypto-side stacks, the same way insider+crowding satisfied it
for equities.

**Episode model, not per-day.** The B-058 classifier fires when *all
three* TVL-stress conditions align (TVL 30d change below threshold, TVL
drawdown from 90d peak above threshold, TVL z-score below threshold). In
real markets a true stress episode persists for weeks — a naive per-day
emit would produce dozens of events for one stress run and clutter
``meta.signal_events``. We emit one event per **episode onset** — the
first day the classifier flips from not-firing → firing — and skip
continued-firing days. Matches the macro_regime_emitter precedent
("emits regime transitions, not continuous state"; see B-096).

**Strength.** Mean of the three normalized excesses (how far each
condition is past its threshold), each saturating at "2× the threshold's
bite":

* TVL 30d change: threshold -10%; saturation at -40% → 30pp range.
* TVL 90d drawdown: threshold 15%; saturation at 45% → 30pp range.
* TVL 90d z-score: threshold -1.0; saturation at -3.0 → 2.0 range.

This captures "how stressed the conditions are at episode onset" without
requiring any single condition to be extreme. Strength 0 = exactly at all
three thresholds; strength 1.0 = all three at saturation. The strength
table is in the module so a re-tune shows up in git blame here (matching
the precedent of ``STRENGTH_SATURATION_REPORTERS`` etc.).

**Chain → asset resolution.** ``CHAIN_TO_CRYPTO_SYMBOL`` maps each
DefiLlama chain name to the watchlist crypto symbol that asset-grounds
the signal. BTC is intentionally absent (B-058 docstring: "Bitcoin TVL
is mostly wrapped BTC + Lightning + Stacks; real BTC price drivers are
macro-led, not on-chain DeFi"). The watchlist provides the sleeve tag
(``crypto:core`` for ETH/SOL, ``crypto:tactical`` for SUI) — emitter
events route into different rules per sleeve, same shape as the
equity-side emitters.

Field mapping per event:

* ``asset``         = native token symbol (ETH / SOL / SUI), resolved via
                      the chain→symbol map.
* ``asset_class``   = ``"crypto"``.
* ``ts``            = the episode-start date at UTC midnight (the first
                      day the classifier flipped to firing).
* ``source``        = ``"tvl_drawdown"`` (matches the source name
                      referenced by any future signal_rules.yml entry).
* ``signal_kind``   = ``"tvl_drawdown_stress"``.
* ``direction``     = ``"bearish"`` (the classifier predicts forward
                      price drawdowns; an "add" cousin is out of scope
                      until / unless B-058's framing extends).
* ``strength``      = mean of normalized excesses; see above.
* ``horizon``       = ``"crypto:{sleeve}"`` from the watchlist
                      (e.g. ``crypto:core`` for ETH / SOL,
                      ``crypto:tactical`` for SUI).
* ``source_ref``    = ``"<chain>:<episode_start_iso>"`` — natural
                      identifier of the stress episode. The UNIQUE
                      constraint on
                      ``(asset, ts, source, signal_kind, source_ref, horizon)``
                      makes re-running over the same data idempotent.

Loading note: the emitter loads the *full* aligned series for each chain
regardless of ``--since``. The feature engineer needs at least 90 days of
prior history to compute the trailing-peak / z-score features, and the
episode-onset detector needs the day-before-since to know whether the
window starts mid-episode. ``--since`` is applied in-Python to the
*emission* window after features are computed.

Run as one ``meta.ingest_runs`` row tagged
``source='signal_emitter' endpoint='tvl_drawdown'`` so the provenance
trail is uniform with the rest of the lake and ``genkei watchlist
health`` surfaces emitter staleness the same way it surfaces ingest
staleness.
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
from genkei.experiments.signal_store import emit_signals_bulk
from genkei.experiments.tvl_drawdown import (
    DEFAULT_TVL_CHANGE_30D_THRESHOLD_PCT,
    DEFAULT_TVL_DRAWDOWN_THRESHOLD_PCT,
    DEFAULT_TVL_ZSCORE_THRESHOLD,
    FeatureRow,
    classifier_fires,
    engineer_features,
    load_aligned_series,
)

EMITTER_SOURCE = "tvl_drawdown"
EMITTER_RUN_TAG = "signal_emitter"
EMITTER_ENDPOINT = "tvl_drawdown"

# Chain → crypto watchlist symbol. Mirrors B-058's
# DEFAULT_CHAIN_PRODUCT_PAIRS but strips the Coinbase product suffix so
# the asset matches the watchlist entry. BTC is intentionally absent
# (B-058 documents why).
CHAIN_TO_CRYPTO_SYMBOL: dict[str, str] = {
    "Ethereum": "ETH",
    "Solana": "SOL",
    "Sui": "SUI",
}

# Saturation magnitudes for the per-condition normalized excesses.
# Each is "the additional bite past the threshold at which strength
# saturates to 1.0 for that condition." Picked from B-058's threshold
# magnitudes (30pp range for the two pct features, 2.0 z-score range
# for the z-score feature).
TVL_CHANGE_30D_SATURATION_RANGE_PCT = Decimal("30")
TVL_DRAWDOWN_SATURATION_RANGE_PCT = Decimal("30")
TVL_ZSCORE_SATURATION_RANGE = Decimal("2")

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmitResult:
    """Return value of ``emit_recent_drawdown_stress`` for CLI / test inspection."""

    ingest_run_id: int
    episodes_emitted: int
    chains_skipped_no_data: int
    chains_skipped_no_watchlist: int


def _feature_ts(feature_row: FeatureRow) -> datetime:
    """Convert a feature row's date to a UTC midnight datetime."""
    return datetime.combine(feature_row.ts, time(0, 0, tzinfo=timezone.utc))


def _horizon_for_symbol(symbol: str, watchlist: Watchlist) -> str | None:
    """Look up the crypto sleeve for a symbol; return None if not watchlisted.

    Returning None when a chain's native token isn't in the watchlist
    matches the equity-side emitters' "non-watchlist issuer → skip"
    behavior — the correlator's asset-grouping needs a stable identifier
    and we don't want to leak unknown assets into the events table.
    """
    entry = watchlist.find_crypto(symbol)
    if entry is None:
        return None
    sleeve = entry.sleeve or "core"
    return f"crypto:{sleeve}"


def _normalized_excess(
    *,
    value: Decimal | None,
    threshold: Decimal,
    saturation_range: Decimal,
    sign: int,
) -> Decimal:
    """Map a feature value's excess past its threshold to a [0,1] saturation.

    ``sign`` is +1 when the threshold is an upper bound (value > threshold
    is the bad side; drawdown_from_peak fits this) and -1 when the
    threshold is a lower bound (value < threshold is the bad side; change
    and z-score both fit this). Returns 0 when value is None or hasn't
    crossed the threshold.
    """
    if value is None:
        return Decimal("0")
    excess = value - threshold if sign > 0 else threshold - value
    if excess <= 0:
        return Decimal("0")
    saturated = excess / saturation_range
    return Decimal("1") if saturated > Decimal("1") else saturated


def _strength_from_features(row: FeatureRow) -> Decimal:
    """Mean of the three TVL-stress conditions' normalized excesses.

    Higher strength = deeper aggregate stress. Each condition contributes
    equally; one condition at full saturation while the other two sit
    near threshold yields strength ≈ 0.33. All three at saturation yields
    strength 1.0.
    """
    change_excess = _normalized_excess(
        value=row.tvl_change_30d_pct,
        threshold=DEFAULT_TVL_CHANGE_30D_THRESHOLD_PCT,
        saturation_range=TVL_CHANGE_30D_SATURATION_RANGE_PCT,
        sign=-1,
    )
    drawdown_excess = _normalized_excess(
        value=row.tvl_drawdown_from_peak_90d_pct,
        threshold=DEFAULT_TVL_DRAWDOWN_THRESHOLD_PCT,
        saturation_range=TVL_DRAWDOWN_SATURATION_RANGE_PCT,
        sign=+1,
    )
    zscore_excess = _normalized_excess(
        value=row.tvl_zscore_90d,
        threshold=DEFAULT_TVL_ZSCORE_THRESHOLD,
        saturation_range=TVL_ZSCORE_SATURATION_RANGE,
        sign=-1,
    )
    return (change_excess + drawdown_excess + zscore_excess) / Decimal("3")


def _detect_episode_starts(
    features: Sequence[FeatureRow],
) -> list[FeatureRow]:
    """Walk features chronologically; return only the rows that begin a new
    stress episode (classifier flipped from not-firing to firing).

    The very first row in the input is treated as a fresh start — if it
    fires, it's counted as an onset (we have no prior to compare to). The
    caller is responsible for loading enough preceding history that the
    "first row" of the input is genuinely far enough back that this
    edge-case treatment is harmless.
    """
    onsets: list[FeatureRow] = []
    prev_fired = False
    for row in features:
        fires = classifier_fires(row)
        if fires and not prev_fired:
            onsets.append(row)
        prev_fired = fires
    return onsets


def _build_event(
    onset: FeatureRow,
    *,
    chain: str,
    asset: str,
    horizon: str,
) -> dict[str, Any]:
    """Build one signal event row from a stress-episode onset."""
    ts = _feature_ts(onset)
    strength = _strength_from_features(onset)
    payload: dict[str, Any] = {
        "chain": chain,
        "asset": asset,
        "horizon": horizon,
        "episode_start": onset.ts.isoformat(),
        "tvl_usd_at_onset": str(onset.tvl_usd),
        "price_usd_at_onset": str(onset.price_usd),
        "tvl_change_30d_pct": (
            str(onset.tvl_change_30d_pct)
            if onset.tvl_change_30d_pct is not None
            else None
        ),
        "tvl_drawdown_from_peak_90d_pct": (
            str(onset.tvl_drawdown_from_peak_90d_pct)
            if onset.tvl_drawdown_from_peak_90d_pct is not None
            else None
        ),
        "tvl_zscore_90d": (
            str(onset.tvl_zscore_90d) if onset.tvl_zscore_90d is not None else None
        ),
        "thresholds": {
            "tvl_change_30d_pct": str(DEFAULT_TVL_CHANGE_30D_THRESHOLD_PCT),
            "tvl_drawdown_from_peak_90d_pct": str(DEFAULT_TVL_DRAWDOWN_THRESHOLD_PCT),
            "tvl_zscore_90d": str(DEFAULT_TVL_ZSCORE_THRESHOLD),
        },
    }
    return {
        "asset": asset,
        "asset_class": "crypto",
        "horizon": horizon,
        "ts": ts,
        "source": EMITTER_SOURCE,
        "signal_kind": "tvl_drawdown_stress",
        "direction": "bearish",
        "strength": strength,
        "payload": payload,
        "source_ref": f"{chain}:{onset.ts.isoformat()}",
    }


def emit_recent_drawdown_stress(
    *,
    since: date | None = None,
    until: date | None = None,
    config: Path = DEFAULT_WATCHLIST_PATH,
) -> EmitResult:
    """Detect TVL-stress episode onsets per chain and emit signal events.

    Loads the full available aligned series for each chain (the feature
    engineer needs ≥90 days of trailing history to compute features, and
    the episode detector needs the prior day's state). ``--since`` /
    ``--until`` are applied to the *emission* window after features +
    onsets are computed in-Python.

    Wrapped in a single ``meta.ingest_runs`` row so the emitter is
    queryable via ``genkei watchlist health`` like any other source.
    """
    watchlist = load_watchlist(config)

    with db.ingest_run(
        EMITTER_RUN_TAG,
        endpoint=EMITTER_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "chains": sorted(CHAIN_TO_CRYPTO_SYMBOL.keys()),
        },
    ) as run:
        events: list[dict[str, Any]] = []
        chains_skipped_no_data = 0
        chains_skipped_no_watchlist = 0
        for chain, symbol in sorted(CHAIN_TO_CRYPTO_SYMBOL.items()):
            horizon = _horizon_for_symbol(symbol, watchlist)
            if horizon is None:
                LOGGER.warning(
                    "TVL-drawdown chain %s maps to %s, which is not in the crypto "
                    "watchlist; skipping signal emission for this chain",
                    chain,
                    symbol,
                )
                chains_skipped_no_watchlist += 1
                continue
            product = f"{symbol}-USD"
            aligned = load_aligned_series(chain, product, until=until)
            if not aligned:
                LOGGER.warning(
                    "TVL-drawdown chain %s has no aligned (TVL, price) data; "
                    "skipping signal emission for this chain",
                    chain,
                )
                chains_skipped_no_data += 1
                continue
            features = engineer_features(aligned)
            onsets = _detect_episode_starts(features)
            for onset in onsets:
                if since is not None and onset.ts < since:
                    continue
                if until is not None and onset.ts > until:
                    continue
                events.append(
                    _build_event(
                        onset, chain=chain, asset=symbol, horizon=horizon
                    )
                )

        rows_written = emit_signals_bulk(events, ingest_run_id=run.id)
        run.add_rows(rows_written)
        return EmitResult(
            ingest_run_id=run.id,
            episodes_emitted=rows_written,
            chains_skipped_no_data=chains_skipped_no_data,
            chains_skipped_no_watchlist=chains_skipped_no_watchlist,
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
        description="Emit TVL-drawdown stress signal events into meta.signal_events."
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
    result = emit_recent_drawdown_stress(since=args.since, until=args.until)
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "episodes_emitted": result.episodes_emitted,
                    "chains_skipped_no_data": result.chains_skipped_no_data,
                    "chains_skipped_no_watchlist": result.chains_skipped_no_watchlist,
                    "source": EMITTER_SOURCE,
                },
                default=_json_default,
            )
        )
    else:
        print(
            f"TVL-drawdown emitter wrote ingest_run_id={result.ingest_run_id} "
            f"episodes={result.episodes_emitted} "
            f"chains_skipped_no_data={result.chains_skipped_no_data} "
            f"chains_skipped_no_watchlist={result.chains_skipped_no_watchlist}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
