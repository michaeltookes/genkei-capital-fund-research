# `genkei momentum`

Trailing **3/7/30-day price momentum** per asset (B-067).

Reads `analytics.price_momentum`, a **materialized** view that precomputes each
watchlist asset's trailing returns — crypto from `coinbase.candles` (`close`),
equity from `yahoo.candles` (`adj_close`, split/dividend-adjusted). The point of
materializing is that this read is a single indexed scan; the momentum math
isn't recomputed on every call.

Each return is `(latest_close - lookback_close) / lookback_close × 100`, where
the lookback is the most recent close **at or before** `latest − N days`
(calendar days). An asset without enough history for a window shows `n/a`
(NULL), never a fake zero.

## Options

```text
--asset         TEXT       Filter to one asset (symbol, e.g. BTC / AAPL).
--asset-class   TEXT       Filter to 'crypto' or 'equity'.
--window        INTEGER    Sort by this window's return: 3, 7, or 30. [default: 7]
--limit         INTEGER    Max rows. [default: 50]
--json                     Emit machine-readable JSON.
--config        PATH       Watchlist path.
--help                     Show this message and exit.
```

## Example

```console
$ genkei momentum --asset-class crypto --window 30
Price momentum | 8 asset(s) | sorted by 30d return
  asset   class   horizon                     date                 close       3d       7d      30d
  PYTH    crypto  crypto:tactical:secondary   2026-07-12          0.0486   +2.32%   +7.28%  +27.89%
  ZEC     crypto  crypto:core:primary         2026-07-12        505.9600   +1.71%  +11.83%  +20.27%
  SOL     crypto  crypto:core:primary         2026-07-12         75.8600   -2.83%   -7.36%  +10.10%
  ETH     crypto  crypto:core:primary         2026-07-12      1,767.5100   -1.57%   -1.72%   +5.19%
  LINK    crypto  crypto:core:primary         2026-07-12          7.9210   -0.54%   -1.22%   -0.76%
  BTC     crypto  crypto:core:primary         2026-07-12     62,379.3100   -2.73%   -2.54%   -3.18%
  SUI     crypto  crypto:tactical:primary     2026-07-12          0.7282   -1.38%   -2.84%   -5.21%
  RENDER  crypto  crypto:tactical:secondary   2026-07-12          1.5110   -3.70%   -6.44%  -14.73%
```

## Refresh

The matview is refreshed daily by `genkei.experiments.refresh_price_momentum`
(`.github/workflows/trend-views-daily.yml`, 13:30 UTC — after the Coinbase and
Yahoo price crons). The refresh runs `REFRESH MATERIALIZED VIEW CONCURRENTLY`
(non-blocking) inside a `meta.ingest_runs` row, so a stale matview surfaces as
STALE in `genkei watchlist health` under the `price_momentum` source.

> Distinct from [`relative-strength`](relative-strength.md): momentum is each
> asset's **own** trailing return; relative-strength is the asset-vs-peer
> *differential*. Because it's a plain view, `genkei query` can also join
> `analytics.price_momentum` onto anything.

## See also

[`relative-strength`](relative-strength.md) · [`anomalies`](anomalies.md) · [`prices`](prices.md)

---

_Example output is a point-in-time capture; shape is stable, values are not._
