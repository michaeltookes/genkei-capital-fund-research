"""TVL drawdown signal_events emitter (B-095)."""

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

# Chain-to-asset routing for watchlist-grounded crypto TVL signals.
CHAIN_TO_CRYPTO_SYMBOL: dict[str, str] = {
    "Ethereum": "ETH",
    "Solana": "SOL",
    "Sui": "SUI",
}

# Per-condition excess ranges where strength saturates.
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
    """Return the crypto sleeve horizon for a watchlisted symbol."""
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
    """Map threshold excess to a [0, 1] saturation score."""
    if value is None:
        return Decimal("0")
    excess = value - threshold if sign > 0 else threshold - value
    if excess <= 0:
        return Decimal("0")
    saturated = excess / saturation_range
    return Decimal("1") if saturated > Decimal("1") else saturated


def _strength_from_features(row: FeatureRow) -> Decimal:
    """Average the three TVL-stress condition excess scores."""
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
    """Return rows where the classifier flips into a stress episode."""
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
    """Detect TVL-stress episode onsets per chain and emit signal events."""
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
