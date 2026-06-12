# Onchain staking events ingester (B-082 / B-086)

Per-event log ingester for Ethereum staking pool contracts. v1 covers
the two Chainlink v0.2 staking pools (Community + Operator); the
schema is generic so adding Lido / RocketPool / EigenLayer is a config
change rather than a schema migration. Designed for **on-demand
backfill + incremental resume-from-last-block**, not a scheduled
daily cron.

## Coverage v1

| Pool | Contract | Address |
|---|---|---|
| Community v0.2 | `CommunityStakingPool` | `0xBc10f2E862ED4502144c7d632a3459F49DFCDB5e` |
| Operator v0.2 | `OperatorStakingPool` | `0xa1d76a7ca72128541e9fcacafbda3a92ef94fdc5` |

Event topics tracked per pool:

- `Staked`
- `Unstaked`
- `UnbondingPeriodStarted`
- `OperatorRemoved`
- `Slashed`

Pool configs live in `DEFAULT_POOLS` in
`src/genkei/ingest/onchain_staking.py`. **B-116** tracks adding the
v0.1 legacy `Staking` contract — different event sigs, deferred.

## Endpoint contract

- **Base URL** — `https://api.etherscan.io/api` (Etherscan V2).
- **Auth** — `ETHERSCAN_API_KEY` env var. Free tier — register at
  <https://etherscan.io/myapikey>.
- **Rate limit** — free tier 5 req/s; collector caps at 4 req/s. With
  ~50k-block chunks (~7 days of Ethereum) and ~2 calls per pool per
  chunk, a full-history backfill from v0.2 deployment (block ~16M)
  completes in tens of minutes.
- **Endpoint** — `GET /api?module=logs&action=getLogs&fromBlock=…
  &toBlock=…&address={pool}&topic0={event_sig}&apikey=…`.
- **Pagination** — Etherscan returns ≤1000 logs per response. The
  collector walks block-range chunks and paginates within a chunk if
  the response is full.

## Schema

- `onchain.staking_events` — fact, PK `(tx_hash, log_index,
  block_timestamp)`. Hypertable on `block_timestamp`, 90-day chunks,
  compressed > 30d.

Generic-by-design columns: `protocol_slug` (chainlink-v02 etc.),
`chain` (ethereum etc.), `contract_address`, `event_type`, `staker`,
`amount_token` (NUMERIC; LINK amount decimal-corrected from the
18-decimal log value), `amount_usd` (nullable until a price join fills
it), plus event-specific decoded fields.

## v1 limitations & known issues

- **No scheduled cron** — designed for on-demand backfill + manual
  incremental resume from the highest stored `block_number`. Wiring a
  daily cron is a config change once steady-state research demand
  exists.
- **Cap-and-intent dynamics (B-082 post-backfill insight)** — the
  Community pool runs at capped capacity (~40.9M LINK). In steady
  state every `Unstaked` is matched by a queued `Staked`, so **net
  flow is structurally zero**. Use `UnbondingPeriodStarted` count as
  the real demand signal (~150/month → ~400/month over 2024-2026).
  Operator pool turnover is much lower (104 Staked + 16 Unstaked vs
  17,488 + 3,141 community) — net-stake-delta is meaningful there.
- **Graceful-skip without key** — if `ETHERSCAN_API_KEY` is unset,
  the collector logs a warning, emits a 0-row `meta.ingest_runs` row,
  exits success. Matches D-020.
- **Single-stage** — no separate normalizer; events parse inline +
  upsert directly. `meta.raw_blobs` stores the JSON page payloads for
  replay.
- **`amount_token` stored in LINK units** — the collector decodes the
  first event-data word and divides by 10^18 before writing. Query
  formulas should use `amount_token` directly with an `event_type`
  `CASE`, without an extra `/ 1e18` conversion.
- **v0.1 legacy pool not covered (B-116)** — 0.46M LINK still staked
  there (~3% of total). Different event topic shape; requires per-
  pool topic override and event-data parser.

## How it runs

- **Manual / on-demand** —
  `python -m genkei.ingest.onchain_staking --backfill --from-block 16083969`
  for an initial backfill from v0.2 deployment, then
  `python -m genkei.ingest.onchain_staking` (no flag) for incremental
  resume-from-highest-stored-block.
- **No GH Actions workflow** today; wire when demand justifies.

## Query path

`genkei query` over `onchain.staking_events`. A typed
`genkei staking --protocol chainlink-v02 --since 2024-01-01` is a
natural future surface.

## Acceptance gates

Before consuming staking-event signals:

1. **Coverage band** — `SELECT MIN(block_number), MAX(block_number),
   MIN(block_timestamp), MAX(block_timestamp) FROM
   onchain.staking_events WHERE protocol_slug = 'chainlink-v02'`
   covers v0.2 deployment forward; missing block ranges signal a failed
   backfill chunk.
2. **PK uniqueness** — `(tx_hash, log_index, block_timestamp)` is the
   declared PK; `SELECT COUNT(*) - COUNT(DISTINCT (tx_hash, log_index,
   block_timestamp))` returning > 0 signals corruption.
3. **Event-type coverage** — `SELECT DISTINCT event_type FROM
   onchain.staking_events WHERE protocol_slug = 'chainlink-v02'`
   matches the v0.2 sig set (Staked / Unstaked / UnbondingPeriodStarted
   / OperatorRemoved / Slashed). New event types signal an upstream
   contract upgrade.
4. **TVL reconciliation** — `SUM(CASE WHEN event_type = 'staked' THEN
   amount_token WHEN event_type = 'unstaked' THEN -amount_token ELSE 0
   END) × LINK_price` from `onchain.staking_events` per pool should
   reconcile **within ~10%** to DefiLlama's `chainlink-staking` TVL row.
   >10% drift investigated.
5. **Graceful-skip documented** — if the latest run skipped due to
   missing `ETHERSCAN_API_KEY`, `meta.ingest_runs.metadata.has_api_key`
   is `false` and rows written is zero.

## Follow-ups

- **B-116** — v0.1 legacy Staking contract. Different topics, requires
  per-pool config + parser extension.
- **Lido / RocketPool / EigenLayer expansion** — same `DEFAULT_POOLS`
  config pattern; no schema migration.
- **Daily cron** — wire when a research session asks for steady-state
  staking-flow signals.
- **`genkei staking` typed CLI subcommand** — today's path is
  `genkei query`.
