# `genkei anomalies`

Per-series **return anomalies** (B-069) — days when an asset's move was a
statistical outlier against its own recent history.

Reads `meta.anomalies`, landed by the anomaly detector
(`genkei.experiments.emitters.anomaly_emitter`). For every watchlist crypto
(`coinbase.candles`) and equity (`yahoo.candles`), the detector converts the
close series to daily returns and flags each day whose return is a rolling
**MAD-based modified z-score** outlier (Iglewicz–Hoaglin), robust to the very
spikes it's hunting. Only flagged days are stored, so this table is sparse by
construction — a handful of flags per asset per year.

The `score` is the signed modified z-score of that day's return versus its
trailing window: positive = `spike_up`, negative = `spike_down`. When a window
is flat enough to degenerate MAD to zero, the detector falls back to a classic
mean/std z-score (`method = zscore`); otherwise `method = modified_zscore`.

## Options

```text
--asset         TEXT       Filter to one asset (ticker or coingecko_id).
--asset-class   TEXT       Filter to 'crypto' or 'equity'.
--direction     TEXT       Filter to 'spike_up' or 'spike_down'.
--since         TEXT       Earliest anomaly date (YYYY-MM-DD).
--until         TEXT       Latest anomaly date (YYYY-MM-DD).
--min-score     FLOAT      Only flags with |score| at or above this.
--limit         INTEGER    Max flags. [default: 50]
--json                     Emit machine-readable JSON.
--help                     Show this message and exit.
```

## Example

```console
$ genkei anomalies --limit 5
Return anomalies | 5 flag(s) | newest first
  asset         class  date          return_%   score  direction    method
  META          equity 2026-06-30       8.81%    4.90  ▲ spike_up   modified_zscore
  VEEV          equity 2026-06-25       8.40%    4.55  ▲ spike_up   modified_zscore
  AAPL          equity 2026-06-24      -6.12%   -4.36  ▼ spike_down modified_zscore
  pyth-network  crypto 2026-06-09      22.19%    6.33  ▲ spike_up   modified_zscore
  SMCI          equity 2026-06-09     -27.98%   -5.90  ▼ spike_down modified_zscore

$ genkei anomalies --asset NVDA --direction spike_down --limit 2 --json
[
  {
    "asset": "NVDA",
    "asset_class": "equity",
    "metric": "daily_return",
    "date": "2025-01-26",
    "value": -0.1697,
    "score": -6.02,
    "method": "modified_zscore",
    "direction": "spike_down",
    "window_days": 90,
    "threshold": 3.5,
    "median": 0.0011,
    "mad": 0.0184
  }
]
```

> `value` is the day's simple return (fraction). `metric` is `daily_return` in
> v1 — the detector is series-agnostic, so a TVL-level or macro-level metric
> can be added alongside without a schema change. See
> `src/genkei/experiments/anomaly_detection.py` for the statistic.

## See also

[`prices`](prices.md) · [`signals`](signals.md) · [`tvl-drawdown`](tvl-drawdown.md)

---

_Example output is a point-in-time capture; shape is stable, values are not._
