# Coinbase Exchange OHLCV Ingester

**B-035.** Daily OHLCV candles from the Coinbase Exchange public REST API. First ingester to break the 365-day ceiling on `coingecko.market_data` — Coinbase candles go back to product-listing date (BTC-USD to 2015-07, ETH-USD to 2016-05) on a free, US-accessible, no-auth endpoint.

```text
$ genkei prices --ticker BTC --source coinbase --since 2015-07-19 --limit 3
BTC prices (source: coinbase, 3 rows)
  timestamp                     price (USD)        market cap            volume
  2026-05-22T19:00:00-05:00       76,933.66               n/a             4,762
  2026-05-21T19:00:00-05:00       75,443.91               n/a             5,132
  2026-05-20T19:00:00-05:00       77,547.62               n/a             6,039
```

## Why Coinbase, not Binance

B-035 was originally scoped as a Binance public-data ingester (the backlog still carries that name). Live investigation found:

- **Binance.com geo-blocks US IPs**, including the homelab Beelink runner and any GitHub-hosted US runner:
  > "Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms"
- **Binance.US** is the US-compliant subset but doesn't list PYTH (one of our secondary tactical alts) and has shorter history for most pairs (Binance.US founded 2019 vs Binance global 2017).
- **Coinbase Exchange** ([api.exchange.coinbase.com](https://api.exchange.coinbase.com)) covers all 7 watchlist coins, no auth required, US-accessible, and gives the longest free history for BTC/ETH.

The backlog name stays as "Binance" for traceability; the shipped implementation is Coinbase. B-035's acceptance criteria all hold:

| Criterion | Status |
|---|---|
| No API key required for public endpoints | ✓ Coinbase Exchange candles are unauthenticated |
| Backfill what's free; document what isn't | ✓ Full free history per product; no paid tier needed |
| Tables aligned with kline structure | ✓ `coinbase.candles` (product, ts, open, high, low, close, volume_base) |

## What's in the lake

After the 2026-05-23 backfill run (`ingest_run_id=108`):

| Product | Days | Earliest | Latest |
|---|---|---|---|
| BTC-USD | 3,961 | 2015-07-19 | 2026-05-22 |
| ETH-USD | 3,656 | 2016-05-17 | 2026-05-22 |
| LINK-USD | 2,523 | 2019-06-26 | 2026-05-22 |
| SOL-USD | 1,802 | 2021-06-16 | 2026-05-22 |
| SUI-USD | 1,102 | 2023-05-17 | 2026-05-22 |
| RENDER-USD | 829 | 2024-02-14 | 2026-05-22 |
| PYTH-USD | 458 | 2025-02-19 | 2026-05-22 |

14,331 rows total. Compare to `coingecko.market_data` (~377 days per coin, ~2,640 rows total) — Coinbase gives roughly 5× more depth for the major pairs.

## Storage

`coinbase.candles` is a TimescaleDB hypertable:

```sql
CREATE TABLE coinbase.candles (
    product         TEXT        NOT NULL,    -- BTC-USD, ETH-USD, …
    ts              TIMESTAMPTZ NOT NULL,    -- candle open time (UTC)
    open            NUMERIC     NOT NULL,
    high            NUMERIC     NOT NULL,
    low             NUMERIC     NOT NULL,
    close           NUMERIC     NOT NULL,
    volume_base     NUMERIC     NOT NULL,    -- base-asset volume (BTC for BTC-USD)
    source_endpoint TEXT        NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
    PRIMARY KEY (product, ts)
);
```

30-day chunks, compression > 30 days, segmented by `product` (the dominant access pattern is per-product history).

**Why `volume_base` not `volume_usd`:** Coinbase returns volume in the base asset (BTC for BTC-USD, ETH for ETH-USD, etc.). Converting to USD requires multiplying by the close price; queries that need USD volume should do that join inline rather than us baking an opinion into the table.

**Market-cap column is intentionally absent.** Exchanges don't carry market-cap data; that's an aggregator concept (`coingecko.market_data.market_cap_usd` is the right source for that).

## Modes

```bash
# Daily collector — 7-day lookback per product. Idempotent (upserts
# overwrite the latest close-price revisions Coinbase publishes within
# 24h of each daily close). Runs on the GH Actions self-hosted Beelink
# runner at 12:00 UTC.
python -m genkei.ingest.coinbase

# Backfill — walks the full history per product in 280-day chunks.
# Per-product earliest-available varies (see table above); pre-listing
# windows return empty arrays which the normalizer accepts silently.
python -m genkei.ingest.coinbase --backfill --since 2015-01-01
python -m genkei.ingest.coinbase --backfill --since 2024-01-01 --until 2024-12-31

# Normalize — picks up the latest collector or backfill run by default.
python -m genkei.normalize.coinbase
python -m genkei.normalize.coinbase --source-run-id 108
```

## API quirks

- **Hard 300-candle cap per call.** Requesting more returns an error dict (`{"message": "granularity too small for the requested time range. Count of aggregations requested exceeds 300"}`) rather than partial data. Backfill walks 280-day chunks to leave headroom.
- **Unusual column order.** Coinbase candles arrive as `[time, low, high, open, close, volume]` — low/high before open/close, not the typical OHLCV. The normalizer unpacks by position; the test suite pins the order so a future API change would surface immediately.
- **Error responses use 200 status.** When the request is malformed (range too long, unknown product, etc.) Coinbase returns HTTP 200 with `{"message": "..."}` instead of a 4xx. The ingester catches dict-shaped responses and records them as failures rather than treating them as 0-row windows.
- **Rate limit: 10 req/sec public, no auth.** We cap at 5 req/sec to leave room for a daily collect to overlap with a slow backfill without tripping the limit.

## CLI

```bash
# Compare prices across sources (coingecko default vs coinbase long-history)
genkei prices --ticker BTC                            coingecko, last 30
genkei prices --ticker BTC --source coinbase          coinbase, last 30
genkei prices --ticker BTC --source coinbase --since 2015-07-19 --limit 5
genkei prices --ticker SUI --source coinbase --json   machine-readable
```

The `--source coinbase` reader returns the same shape as the coingecko reader (`ts` / `price_usd` / `market_cap_usd` / `volume_usd`) with `market_cap_usd = null` so the formatter doesn't need to branch. `price_usd` is the candle close; `volume_usd` is the base-asset volume (the table column is `volume_base` — the CLI alias matches the coingecko shape for renderer compatibility, but the unit is asset-denominated, not USD).

## What this unblocks

- **B-058** (TVL drawdown early-warning model) — was blocked on long-history crypto prices. 10y of BTC, ETH, LINK, SOL prices in the lake means an OOS validation can finally span multiple macro regimes (2018 bear, 2020 COVID, 2021 boom, 2022 hiking + bear, 2023 recovery, 2024-25 bull).
- **B-059** (macro regime classifier) — was descriptive-only. Pairing the regime labels against forward returns becomes an empirical question now that prices exist for the windows the regime classifier covers.
- **B-061** (13F crowding monitor) — needs price moves to score "smart money piling in vs early."

## What this does NOT cover

- **Equity prices.** Coinbase only lists crypto. B-039 (equity price ingester) is still open.
- **Intraday candles.** Daily granularity (`granularity=86400`) only; we don't currently need 1m / 5m / 1h candles.
- **Order book / trade tape.** Public candles only; orderbook + trades are public but not in scope for the lake's "daily-resolution time series" pattern.
- **Coinbase-specific fields.** No quoteAssetVolume, no taker buy/sell split (those exist on Binance's kline endpoint but not on Coinbase's candle endpoint).

## Schema-drift canary

A `coinbase` entry exists in `genkei.common.schema_drift.SCHEMA_SPECS` (B-072) — pattern `candles\_%`, payload type `array`, `array_item_min_length=6`. Two drift modes are flagged automatically by `genkei watchlist drift`:

1. Coinbase changes the column count (the 6-element array gets shorter or longer).
2. Coinbase switches from array-of-arrays to array-of-objects (which would silently drop every row in the normalizer).

The drift check confirmed the spec matches every freshly-landed blob from `ingest_run_id=106` and `108`.
