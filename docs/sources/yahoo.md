# Yahoo Finance ingester (B-092)

Daily OHLCV candles for every equity in the watchlist (core + benchmarks
+ ETFs). Counterpart to the Coinbase ingester (B-035) on the equity
side. Yahoo's public chart endpoint is **US-accessible, keyless, and
covers 45+ years of history** — AAPL goes back to 1980-12-12 in one
call.

## Coverage v1

Three groups in the watchlist:

- **Equities (core + tactical)** — currently ~12 names spanning AAPL,
  GOOGL, MSFT, META, NVDA, BRK-B, V, JNJ, PG, KO, plus crypto-adjacent
  picks.
- **Benchmarks** — SPY, QQQ, IWM (equity-index ETFs used as
  comparators for the relative-strength emitter, B-098).
- **Crypto ETFs** — IBIT, ETHA, FBTC, ARKB, etc. (B-105 pivot from
  Farside scrape after Cloudflare-walling).

The `equities:` / `benchmarks:` / `etf_tickers:` sections in
`src/genkei/data/watchlists.yml` curate the full list.

## Endpoint contract

- **Base URL** — `https://query1.finance.yahoo.com`.
- **Auth** — none.
- **Rate limit** — community ceiling ~1-2 req/s; collector caps at
  2 req/s. Yahoo will 429 on bursty / undifferentiated User-Agent
  requests — the collector sends a browser-flavored UA.
- **Endpoint** — `GET /v8/finance/chart/{ticker}?interval=1d
  &period1=…&period2=…`. Single call returns full range; no pagination.

## Schema

- `yahoo.candles` — time-series fact, PK `(ticker, ts)`. Hypertable on
  `ts`, 30-day chunks, compressed > 30d, segmentby `ticker`.

Columns: `open`, `high`, `low`, `close`, `adj_close`, `volume`.
**`adj_close` is the correct input** for return / drawdown / regression
analysis (handles splits + dividends + spin-offs). **`close` is the
input** for notional-traded calculations or "what showed on the tape."

## v1 limitations & known issues

- **Browser User-Agent required** — Yahoo flags scripted UAs as 429.
  The collector sends Chrome-flavored UA; if Yahoo tightens, swap to a
  rotating UA pool or pivot to the Stooq fallback documented in B-092.
- **`adj_close` rarely NULL** — only for recent IPOs or delisted
  tickers (no split / dividend trail). Surface to the consumer rather
  than silently substituting `close`.
- **Daily-only** — `interval=1d`. Intraday candles deferred.
- **No auth header to redact** — keyless, no leakage path.
- **Trailing-window mode** — daily run fetches trailing **14 days**
  per ticker (longer than Coinbase's 7d window) to absorb US equity
  holidays / weekends / occasional Yahoo gaps.
- **Currency assumed USD** — the watchlist's equity universe is
  US-listed today; a non-USD listing would need an explicit currency
  column.

## How it runs

- **Daily workflow** — `.github/workflows/yahoo-daily.yml`, cron
  `15 12 * * *` (12:15 UTC). Sequenced between Coinbase + the macro
  pulls.
- **Two-stage** — collect → `meta.raw_blobs` (one blob per ticker) →
  normalize → `yahoo.candles`.
- **Backfill** — `python -m genkei.ingest.yahoo --backfill` fetches
  full listing-date-to-today with `period1=0` per ticker. AAPL ≈ 11k
  candles per call (~3 MB, ~1.5s).

## Query path

`genkei query` over `yahoo.candles` and the typed `genkei prices
--ticker AAPL --since 2024-01-01` subcommand.

## Acceptance gates

Before consuming Yahoo-derived equity signals:

1. **Freshness** — `meta.ingest_runs.finished_at` for the latest
   `(yahoo, collect)` + `(yahoo, normalize)` rows is within 36 hours.
2. **Every watchlist ticker covered** — `SELECT DISTINCT ticker FROM
   yahoo.candles` matches the union of `equities` + `benchmarks` +
   `etf_tickers` + `yahoo_price_targets` from the watchlist. The
   `yahoo_price_targets` section is price-only coverage; those tickers
   should not be treated as equity research targets.
3. **Per-ticker latest ts on the most recent NYSE / NASDAQ trading
   day** — gap > 5 calendar days during regular market sessions
   signals a delist or API drift.
4. **No partial-endpoint failures** — `metadata.partial_endpoints` is
   empty for the latest run. A 429 burst is a partial failure.
5. **`adj_close` populated** — `SELECT COUNT(*) FROM yahoo.candles
   WHERE adj_close IS NULL AND ts > NOW() - INTERVAL '30 days'`
   returns 0 for established tickers. Recent IPOs / pre-listing windows
   are exempt.
6. **OHLC sanity** — `low <= open <= high` and `low <= close <= high`
   on every row.

## Follow-ups

- **Stooq fallback** — documented in B-092 if Yahoo tightens UA
  restrictions; not yet wired.
- **Intraday candles** — `interval=5m` / `1h` for event-study windows
  around earnings / 8-K announcements.
- **Multi-currency support** — explicit `currency` column when
  ingesting non-US listings.
- **Corporate-actions endpoint** — Yahoo exposes split / dividend
  history; a parallel `yahoo.corporate_actions` table would let the
  lake reconstruct adjustments without trusting Yahoo's
  `adj_close` directly.
- **B-111 / B-098 relative-strength** — already consumes
  `yahoo.candles` via the equity emitter; documented here for
  future-maintainer context.
