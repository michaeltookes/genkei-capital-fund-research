# Coinbase Exchange ingester (B-035)

Daily OHLCV candles for every watchlist crypto with a Coinbase product
ID. B-035 originally targeted Binance; pivoted to Coinbase Exchange in
2026 after `api.binance.com` enforced a US geo-block on the
GH-Actions hosted runner. Coinbase is US-accessible, keyless, and
covers all seven watchlist coins (BTC, ETH, SOL, LINK, SUI, PYTH, DOGE).

## Coverage v1

Per-product daily candles for each watchlist entry carrying a non-null
`coinbase_product` field:

| Product | Earliest available |
|---|---|
| BTC-USD | 2015-07-20 |
| ETH-USD | 2016-05-18 |
| SOL-USD | 2020-04-10 |
| LINK-USD | 2019-06-26 |
| SUI-USD | 2023-05-04 |
| PYTH-USD | 2023-11-22 |
| DOGE-USD | 2021-06-03 |

Any coin without a `coinbase_product` (set in `src/genkei/data/watchlists.yml`)
is silently skipped with a logged note — keeps daily runs clean while
the watchlist is incomplete.

## Endpoint contract

- **Base URL** — `https://api.exchange.coinbase.com`.
- **Auth** — none. Public-tier endpoint.
- **Rate limit** — documented 10 req/s ceiling; collector caps at
  5 req/s for headroom.
- **Endpoint** — `GET /products/{product}/candles?granularity=86400
  &start=YYYY-MM-DDTHH:MM:SSZ&end=…`.
- **Hard cap** — 300 candles per response. Collector chunks backfill
  at 280-day windows for safety headroom.
- **Column order** — Coinbase returns `[ts, low, high, open, close, volume]`
  — note **low/high before open/close**. The parser pins this order;
  watch for any API change.

## Schema

- `coinbase.candles` — time-series fact, PK `(product, ts)`. Hypertable
  on `ts`, 30-day chunks, compressed > 30d, segmentby `product`.

Columns: `open`, `high`, `low`, `close` (all NUMERIC), `volume`
(NUMERIC; native unit varies by product). Stored without adjustment —
Coinbase candles are spot-price, no split / dividend / fee adjustment
to apply.

## v1 limitations & known issues

- **Daily granularity only** — `granularity=86400`. Intraday candles
  (60s, 5m, 1h) are exposed by Coinbase but not in v1 scope.
- **300-candle hard cap** — backfill chunks at 280 days. Daily mode
  fetches trailing 7 days per product.
- **Coinbase republishes within 24h** — recent closes may revise
  upward / downward by a small amount as late-settled fills land.
  Daily upsert handles this naturally via the natural PK.
- **No auth header to redact** — keyless. No G-021-style leakage path.
- **Per-product history varies** — pre-listing windows return 400. The
  collector silently skips empty responses; the watchlist's per-product
  earliest available date is documentation-only (no guardrail).

## How it runs

- **Daily workflow** — `.github/workflows/coinbase-daily.yml`, cron
  `0 12 * * *` (12:00 UTC). Sequenced after CoinGecko.
- **Two-stage** — collect → `meta.raw_blobs` (one blob per product) →
  normalize → `coinbase.candles`.
- **Backfill** — `python -m genkei.ingest.coinbase --backfill --since
  2015-01-01 --until 2026-06-12` walks 280-day chunks per product. One
  pass per product, oldest first.

## Query path

`genkei query` over `coinbase.candles`. The shared `genkei prices` CLI
subcommand currently resolves crypto prices through CoinGecko; a future
cross-source reconciliation would join both tables on `(coingecko_id ↔
coinbase_product, ts)`.

## Acceptance gates

Before consuming Coinbase candles in a research session:

1. **Freshness** — `meta.ingest_runs.finished_at` for the latest
   `(coinbase, collect)` + `(coinbase, normalize)` rows is within 36
   hours.
2. **Every watchlist coin with a `coinbase_product` covered** —
   `SELECT DISTINCT product FROM coinbase.candles` matches the watchlist
   product set.
3. **Per-product latest ts within 1 day** — Coinbase publishes daily;
   gaps >1 calendar day indicate a delist or API drift.
4. **No partial-endpoint failures** — `metadata.partial_endpoints` is
   empty for the latest run.
5. **OHLC sanity** — `low <= open <= high` and `low <= close <= high`
   for every row. A failing row signals the column-order regression
   on the Coinbase side.

## Follow-ups

- **Intraday candles** — drop `granularity=86400` and ingest 60s /
  300s / 3600s windows when a high-frequency research question
  warrants it. Separate table to avoid PK collision.
- **Cross-source reconciliation** — pair `coinbase.candles` with
  `coingecko.market_data` on the four overlapping coins (BTC, ETH,
  SOL, LINK) and flag any source-side divergence > 0.5% / day.
- **Fallback to Stooq** — documented in B-035 if Coinbase ever proves
  unreliable from the runner; not yet wired.
