# DeFiLlama ingester (B-001 → B-035 lineage)

DeFiLlama publishes open-access DeFi data — chain-level TVL, per-protocol
TVL, stablecoin supply, protocol fees / revenue, and asset prices —
without authentication. This was the **first ingester** in the lake and
remains the canonical pattern every subsequent collector follows
(`collect` → `meta.raw_blobs` → `normalize` → typed upsert).

## Coverage v1

| Dataset | Endpoint | Cadence | Table |
|---|---|---|---|
| **Chain TVL** | `/v2/chains` + `/v2/historicalChainTvl/{chain}` | Daily | `defillama.chain_tvl` |
| **Protocol metadata** | `/protocols` | Daily | `defillama.protocols` |
| **Per-protocol TVL** | `/protocol/{slug}` | Daily / backfill | `defillama.protocol_tvl` |
| **Protocol fees / revenue** | `/summary/fees/{slug}?dataType=dailyFees` + `dailyRevenue` | Daily full-history refresh | `defillama.protocol_fees` |
| **Stablecoin supply** | `/stablecoins` + `/stablecoin/{id}` | Daily / backfill | `defillama.stablecoins` |
| **Asset prices** | `/prices/current/{ids}` + `/coins/prices/historical/{ts}/{ids}` | Daily / backfill | `defillama.prices` |

The watchlist's `protocols:` section (see `src/genkei/data/watchlists.yml`)
curates which protocol slugs the per-protocol pulls cover; chain TVL +
stablecoins are global (every chain / every stablecoin DeFiLlama exposes).

## Endpoint contract

- **Base URLs** — `api.llama.fi`, `coins.llama.fi`, `stablecoins.llama.fi`.
- **Auth** — none. Public API.
- **Rate limit** — undocumented; community ceiling ≈ 5-10 req/s.
  We default to **2 req/s** as the polite "be reasonable" floor.
- **Response shape** — JSON. Per-entity endpoints return either
  `{ tvl: [...] }` (chain / protocol history) or
  `{ peggedAssets: [...] }` (stablecoins). The collector preserves the
  raw envelope in `meta.raw_blobs` so the normalizer can replay.

## Schema

- `defillama.protocols` — entity dim, PK `slug`. Captures the
  DeFiLlama-published metadata (name, category, chains, parent, …).
- `defillama.chain_tvl` — time-series fact, PK `(chain, ts)`. Day-aligned
  to UTC midnight (post-20260604 dedup migration).
- `defillama.protocol_tvl` — time-series fact, PK `(slug, chain, ts)`.
- `defillama.protocol_fees` — time-series fact, PK `(slug, ts)`.
  Carries both `fees_usd` and `revenue_usd` from the protocol fees /
  revenue blobs.
- `defillama.stablecoins` — time-series fact, PK `(asset_id, chain, ts)`.
- `defillama.prices` — time-series fact, PK `(asset_key, ts)`.

Hypertables on `ts` where time-series, 30-day chunks, compressed > 30d.

## v1 limitations & known issues

- **Day-alignment migration (20260604)** — the chain_tvl + prices tables
  always stored UTC-midnight timestamps; stablecoins / protocol_tvl /
  protocol_fees originally carried sub-day precision which corrupted the
  PK uniqueness guarantee. The 20260604 migration normalized those tables
  and the collector now day-aligns on write.
- **Backfill is per endpoint family, not global** — `--backfill --since
  YYYY-MM-DD` supports repeatable `--endpoint prices`, `--endpoint
  protocols`, and `--endpoint stablecoins` filters. Omit `--endpoint` to
  run all three. Daily incremental refresh is the default mode with no
  `--backfill` / `--endpoint` flags.
- **Bitcoin ecosystem labelling** — DeFiLlama categorizes generic CEX +
  custodial BTC exposure under "Bitcoin", which pollutes any Bitcoin-
  ecosystem TVL aggregation. The downstream daily-brief logic and the
  `config/defillama.sources.json` exclusion keywords handle the filter;
  any new CEX/custodial label needs to land in that config (B-020).
- **No vendor-side audit trail** — DeFiLlama overwrites historical TVL in
  place when they re-curate protocols. We capture *what we saw on
  fetch* in `meta.raw_blobs`; the lake doesn't reconstruct prior
  DeFiLlama-side revisions.

## How it runs

- **Daily workflow** — `.github/workflows/defillama-daily.yml`, cron
  `30 10 * * *` (10:30 UTC). First in the daily ingest train.
- **Two-stage** — `python -m genkei.ingest.defillama` collects, parses
  the printed `ingest_runs id`, and passes it as `--source-run-id` to
  `python -m genkei.normalize.defillama`.
- **Backfill** — manual `python -m genkei.ingest.defillama --backfill
  --since 2024-01-01 --endpoint protocols` (or `prices` /
  `stablecoins`; repeat the flag for multiple families, or omit it for
  all). `protocols` lands per-protocol TVL history only; fees / revenue
  history lands from the daily collector's protocol fees and revenue
  blobs. `prices` lands historical price blobs, and `stablecoins` lands
  stablecoin supply history. Chain TVL history is not a backfill selector
  because the daily collector already pulls full per-chain history.

## Query path

`genkei query` over `defillama.*`. Brief generation lives in
`scripts/build_daily_report.py` (legacy DeFiLlama MVP); the modern
research path uses CLI-driven on-demand queries directly against
`defillama.chain_tvl` / `defillama.protocol_tvl` etc.

## Acceptance gates

Mirrors `docs/defillama-daily-review.md`. Before consuming a generated
DeFiLlama snapshot as decision support:

1. **Freshness** — `meta.ingest_runs.finished_at` for the latest
   `(defillama, collect)` row is within 36 hours; same for `normalize`.
2. **Row counts within band** — `defillama.chain_tvl` gains ≥ N
   rows per day (N ≈ count of tracked chains); `defillama.prices`
   gains ≥ N rows per day (N ≈ watchlist coin count). Stale or empty
   gains → log a contract-drift issue.
3. **Day-aligned timestamps** — `defillama.chain_tvl.ts` is always
   midnight UTC; `defillama.prices.ts` is always midnight UTC. Sub-day
   precision is a regression of the 20260604 migration.
4. **Bitcoin exclusion list applied** — the chain TVL aggregator's
   Bitcoin row excludes CEX + custodial categories per
   `config/defillama.sources.json`.
5. **No partial-endpoint failures** — `meta.ingest_runs.metadata.partial_endpoints`
   for the latest run is empty.

## Follow-ups

- **B-001 / B-002** — persist generated briefs back to the repo +
  publish to Mission Control / Research surface (rescope post-Phase 1).
- **B-020** — move Bitcoin CEX/custody exclusion keywords from
  hardcoded constants in `scripts/normalize_defillama.py` to
  `config/defillama.sources.json`.
- **B-023** — surfaced freshness warning when latest snapshot is >N
  hours stale.
- **B-025** — decide fate of the auto-generated daily brief now that
  CLI-driven on-demand briefs exist.
- **Per-protocol fees backlog** — extend the per-protocol fees fetch
  beyond the current watchlist as research demand grows.

## DePIN fees: Render Network BME (B-128)

`render-network-bme` (category DePIN, chain Solana) is a **fees-only**
protocol slug — no TVL on DefiLlama, same shape as `chainlink-requests`.
It carries Render's Burn-and-Mint Equilibrium fees+revenue as the
compute-demand proxy for the RENDER thesis. The intuitive slugs
(`render` / `render-network` / `rndr`) all 404 — pin `render-network-bme`.
Full survey, coverage limits (fee-value not raw usage; history from
~2025-06), and query examples live in `docs/sources/render-usage.md`.
