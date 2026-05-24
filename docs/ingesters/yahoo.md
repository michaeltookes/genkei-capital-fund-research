# Yahoo Finance Equity OHLCV Ingester

**B-092.** Daily OHLCV candles from Yahoo Finance's public chart endpoint. Equity counterpart to B-035 (Coinbase, crypto). Free, no auth, US-accessible, full history per ticker in a single request.

```console
$ genkei prices --ticker AAPL --since 1980-12-12 --limit 3
AAPL prices (source: yahoo, 3 rows)
  timestamp                     price (USD)        market cap            volume
  2026-05-22T08:30:00-05:00          308.82               n/a        43,627,900
  2026-05-21T08:30:00-05:00          304.99               n/a        42,965,100
  2026-05-20T08:30:00-05:00          302.25               n/a        38,229,800
```

## What's in the lake

After the 2026-05-24 backfill run (`ingest_run_id=123`):

| Ticker | Days | Earliest |
|---|---|---|
| XOM | 14,219 | 1970-01-02 |
| AMD | 11,641 | 1980-03-17 |
| JPM | 11,641 | 1980-03-17 |
| AAPL | 11,453 | 1980-12-12 |
| MU | 10,576 | 1984-06-01 |
| MSFT | 10,127 | 1986-03-13 |
| CCJ | 7,597 | 1996-03-14 |
| AMZN | 7,301 | 1997-05-15 |
| TSM | 7,201 | 1997-10-08 |
| NVDA | 6,650 | 1999-01-22 |
| GOOG / GOOGL | ~5,470 | 2004-08-19 |
| V | 4,650 | 2008-03-19 |
| AVGO | 4,250 | 2009-08-06 |
| TSLA | 3,990 | 2010-06-29 |
| META | 3,560 | 2012-05-18 |
| BMNR / others | varies | listing date |

**158,311 rows total** across 28 equity tickers. Compare to `coinbase.candles` (14,331 rows, 7 crypto products) — Yahoo gives ~11× more rows because there are 4× more tickers and equity history goes back further.

## Storage

`yahoo.candles` is a TimescaleDB hypertable:

```sql
CREATE TABLE yahoo.candles (
    ticker          TEXT        NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,    -- daily candle (UTC)
    open            NUMERIC     NOT NULL,
    high            NUMERIC     NOT NULL,
    low             NUMERIC     NOT NULL,
    close           NUMERIC     NOT NULL,    -- unadjusted (tape price)
    adj_close       NUMERIC,                 -- split-and-dividend-adjusted (NULL only on very new IPOs)
    volume          NUMERIC     NOT NULL,
    source_endpoint TEXT        NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
    PRIMARY KEY (ticker, ts)
);
```

30-day Timescale chunks, compression > 30 days, segmented by ticker.

**Storing both `close` and `adj_close`.** Unadjusted close is what showed on the tape that day (volume × close = reported notional traded). `adj_close` is Yahoo's split-and-dividend-adjusted price — the right input for return / drawdown / regression calculations. The CLI prefers `adj_close` for `price_usd` and surfaces the raw `close` as `close_unadjusted` in JSON; SQL queries pick whichever they need.

**No market-cap column.** Yahoo's chart endpoint doesn't carry market cap. If we ever need it, the `/quote/{ticker}` endpoint does, but that's a separate ingest (and the current scoring rubric doesn't read market cap).

## Modes

```bash
# Daily collector — 14-day lookback per ticker. Longer than Coinbase's
# 7d to absorb equity-market holidays + weekends + occasional Yahoo
# gaps. Runs on the GH Actions self-hosted Beelink runner at 12:15 UTC.
python -m genkei.ingest.yahoo

# Backfill — one call per ticker with period1=0 (epoch) pulls the
# full listing-date-to-today history in a single response. AAPL =
# 11,453 candles in ~3MB / ~1.5s. No chunking needed.
python -m genkei.ingest.yahoo --backfill
python -m genkei.ingest.yahoo --backfill --since 2000-01-01 --until 2024-12-31

# Normalize — picks up the latest collector or backfill run.
python -m genkei.normalize.yahoo
python -m genkei.normalize.yahoo --source-run-id 123
```

## API quirks

- **No chunking required.** Unlike Coinbase's hard 300-candle cap, Yahoo serves arbitrarily long ranges in one response. AAPL's 45y of daily history is one ~3MB JSON response.
- **Browser-flavored User-Agent.** Yahoo occasionally returns 429 for sparse / empty UAs. The ingester sends `Mozilla/5.0 (compatible; genkei-research/1.0; +https://github.com/)` — stays well below any moderate-bot heuristic.
- **Parallel arrays, not array-of-objects.** Response shape is `{chart: {result: [{timestamp, indicators: {quote: [{open, high, low, close, volume}], adjclose: [{adjclose}]}}]}}`. Each of `timestamp` / `open` / `high` / `low` / `close` / `volume` / `adjclose` is a parallel array indexed by position. The normalizer zips by index — Yahoo guarantees alignment in practice.
- **Errors use HTTP 200 with `chart.error` set.** Yahoo doesn't 4xx malformed requests; it returns `{chart: {error: {code, description}, result: null}}` with status 200. The ingester catches the `error` key and records as a failure rather than treating the empty `result` as 0 rows.
- **Adjclose can be null for very new tickers.** Brand-new IPOs sometimes lack the adjusted-close calculation; `adj_close` in the table is nullable to preserve this honestly.
- **Blob prefix is `chart_<ticker>`, not `candles_`.** Reason: B-035's Coinbase ingester already owns `candles_%` in the B-072 schema-drift specs. Yahoo uses `chart_` to mirror its endpoint name (`/v8/finance/chart/<ticker>`) and avoid the LIKE-pattern collision.

## CLI

```bash
genkei prices --ticker AAPL                       # routes to Yahoo (default for equities)
genkei prices --ticker AAPL --since 1980-12-12    # full history
genkei prices --ticker MSFT --json                # machine-readable
genkei prices --ticker BTC --source yahoo         # loud error — wrong asset class
genkei prices --ticker AAPL --source coingecko    # loud error — wrong asset class
```

Equity tickers default to `--source yahoo`; crypto tickers default to `--source coingecko`. Explicit `--source` mismatches (crypto-on-yahoo or equity-on-crypto-source) error loudly with actionable routing hints.

The reader returns `price_usd` as `adj_close` (the right thing for return calculations), with the raw `close` preserved as `close_unadjusted` in JSON output.

## What this unblocks

- **B-065 v2 watchlist scoring rubric** — the equity sleeve (`insider_flow` + `revenue_trend` + `filings_velocity`) finally has forward-return data to validate against. Future scoring iterations can compute regime-conditional precision/recall the same way B-058 did for crypto.
- **B-057 (8-K filing impact study)** — Phase 5 experiment, was completely blocked on equity prices; now runnable.
- **B-061 (13F crowding monitor)** — needs price moves to score smart-money timing. Still gated on B-080 (13F ingester) but the price half is done.
- **Regime-conditional return study** (filed as a B-059 follow-up) — pair the macro regime labels (`risk_on` / `risk_off` / `easing` / `tightening_stress` / `mixed`) against forward equity returns and see if there's regime-dependent alpha. Was blocked on this ingester.
- **`/reflect-decisions` skill on equity decisions** — `docs/research/decisions/*.md` files that pinned equity tickers can finally have their `## Outcome` blocks computed by `genkei prices --ticker <T>` lookups.

## What this does NOT cover

- **Intraday candles.** Daily granularity only.
- **Pre-listing / delisted tickers.** Yahoo returns whatever's still listed.
- **Foreign-market tickers needing suffixes** (BRK-B, BHP.AX, etc.). Today's watchlist is all US-listed symbols — if a future ticker needs a Yahoo-specific suffix, the watchlist can grow a `yahoo_ticker` field. Out of scope for v1.
- **Corporate actions detail.** `adj_close` bakes in splits + dividends but the actions themselves aren't surfaced. Yahoo's `/v7/finance/download/<ticker>?events=split,div` endpoint would carry that — separate ingest if needed.
- **Real-time / last-trade.** Daily close only.

## Schema-drift canary

A `yahoo` entry exists in `genkei.common.schema_drift.SCHEMA_SPECS` (B-072 / B-092 extension). Pattern `chart\_%`, payload type `object`, required key `chart`, nested path `chart.result`. Catches the two realistic drift modes:

1. Yahoo removes or renames the `chart` wrapper.
2. Yahoo returns a malformed response where `result` is missing.

The drift check confirmed the spec matches every freshly-landed blob from `ingest_run_id=123` (the backfill).

## What this branch deliberately does NOT ship

Captured here so the next ship knows what's left:

- **`yahoo_ticker` watchlist field.** Not needed for any current watchlist entry. Add when the first foreign-market ticker lands.
- **Corporate-actions ingester.** Splits + dividends are baked into `adj_close`; the events themselves are a future ingest if anyone asks.
- **CLI source-comparison helper.** A `genkei prices --compare` flag that side-by-side dumps yahoo + coingecko for a ticker (where both are valid) would be a small follow-up. Not in scope today.
