# CoinGecko ingester (B-034)

CoinGecko's public API provides per-coin metadata and daily
price / market-cap / volume history for every watchlist crypto.
**Tier-dependent backfill** — the Demo tier ships rolling 365-day
history; the Pro tier opens bounded date-range queries and is the
backfill path. Daily mode can run without a key against CoinGecko's
public host, but at a much lower rate limit; deep backfill still
requires a Pro key.

## Coverage v1

Every entry in the watchlist's `crypto:` sleeves is fetched. v1 covers:

- **Core hold** — BTC, ETH, SOL, LINK.
- **Tactical primary** — SUI.
- **Tactical secondary** — PYTH, RENDER (and any additions).
- **Protocol-side companions** — sourced from `protocols:` entries
  carrying a `coingecko_id` (B-062), e.g. CRV, AAVE, MKR.

The watchlist binds each coin to a stable `coingecko_id` so renames
on the CoinGecko side don't break ingestion.

## Endpoint contract

- **Demo tier** — base URL `https://api.coingecko.com/api/v3`, header
  `x-cg-demo-api-key: <key>`, ~25 req/min ceiling.
- **Pro tier** — base URL `https://pro-api.coingecko.com/api/v3`,
  header `x-cg-pro-api-key: <key>`, opens
  `/coins/{id}/market_chart/range` for bounded date queries.
- **Keyless** — public host, ~5 req/min, no auth header. Daily metadata
  and rolling market-chart fetches still run live when
  `COINGECKO_API_KEY` is unset; Pro backfill is rejected without a key.
- **Endpoints used** — `/coins/{id}` (metadata),
  `/coins/{id}/market_chart` (rolling 365d, Demo / Pro), and
  `/coins/{id}/market_chart/range` (bounded ranges, Pro only).
- **Rate limit** — collector defaults to 2 req/s; well under either tier.

## Schema

- `coingecko.coins` — entity dim, PK `coingecko_id`.
- `coingecko.market_data` — time-series fact, PK `(coingecko_id, ts)`.
  Hypertable on `ts`, 30-day chunks, compressed > 30d, segmentby
  `coingecko_id`.

Columns include `price_usd`, `market_cap_usd`, `volume_usd`,
`source_endpoint`, `fetched_at`, and `ingest_run_id`.

## v1 limitations & known issues

- **Demo tier 365-day ceiling** — `/coins/{id}/market_chart` returns
  exactly the rolling last 365 days. Backfill beyond that requires
  Pro tier + `/market_chart/range`.
- **Keyless still fetches live** — if `COINGECKO_API_KEY` is missing,
  the collector logs a warning, records `metadata.authenticated=false`,
  uses the public host at `KEYLESS_RATE_LIMIT` (~5 req/min), and fetches
  daily metadata + rolling market charts. It is slower, not a 0-row skip.
- **API key never on the wire log** — keys land in the request header,
  not the URL. The collector double-checks by redacting any incidental
  key occurrence in error messages before they hit the partial-endpoint
  log.
- **429 retry behavior** — Demo/keyless tiers can rate-limit even within
  documented bounds. The collector uses `HttpClient`'s default
  `RetryPolicy`: status 429 is retryable, `Retry-After` is honored when
  present, and exponential backoff + jitter runs up to 4 attempts
  (~3 retries).

## How it runs

- **Daily workflow** — `.github/workflows/coingecko-daily.yml`, cron
  `45 11 * * *` (11:45 UTC). Sequenced after FRED.
- **Two-stage** — collect → `meta.raw_blobs` → normalize →
  `coingecko.coins` / `coingecko.market_data`.
- **Backfill** — `python -m genkei.ingest.coingecko --backfill --since
  YYYY-MM-DD` (Pro key required). Default backfill window is ~1 year.

## Query path

`genkei query` over `coingecko.*` and the typed `genkei prices --ticker
BTC --since 2024-01-01` subcommand (sources from `coingecko.market_data`).

## Acceptance gates

Before consuming CoinGecko-derived signals:

1. **Freshness** — `meta.ingest_runs.finished_at` for the latest
   `(coingecko, collect)` and `(coingecko, normalize)` rows is within
   36 hours. Keyless runs should still produce rows; check
   `metadata.authenticated=false` when diagnosing slower public-mode runs.
2. **Every watchlist coin covered** — distinct `coingecko_id` count in
   `coingecko.market_data` matches `len(watchlist.crypto) +
   count(watchlist.protocols with coingecko_id)`.
3. **Per-coin latest observation within 3 days** — CoinGecko publishes
   daily; gaps >3 days indicate a coin renamed / delisted / API drift.
4. **No partial-endpoint failures** — per-coin fetches captured in
   `metadata.partial_endpoints` are empty.
5. **Auth mode explicit** — latest collect metadata records
   `authenticated` and `api_tier`. `authenticated=false` means the run
   used public keyless mode; it should not be treated as an intentional
   skip.

## Follow-ups

- **Pro-tier backfill** — automate the bounded-range walker so deep
  history lands without operator intervention.
- **Per-coin sparkline / community data** — CoinGecko exposes
  community-engagement metrics + developer-activity scores; deferred
  until a research session asks for them.
- **Cross-source price reconciliation** — pair `coingecko.market_data`
  with `coinbase.candles` for the four overlapping coins (BTC, ETH,
  SOL, LINK) to flag any source-side divergence > 0.5%.
