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
| Legacy v0.1 (B-116) | `Staking` | `0x3feB1e09b4bb0E7f0387CeE092a52e85797ab889` |

Event topics tracked per pool (v0.2):

- `Staked`
- `Unstaked`
- `UnbondingPeriodStarted`
- `OperatorRemoved`
- `Slashed`

v0.1 (B-116) tracks `Staked` / `Unstaked` / `Migrated`. Its events differ
from v0.2 in a load-bearing way — **the staker is not indexed** (it's in
`data` word 0, not `topics[1]`) and the principal delta is `data` word 1 — and
v0.1 `Unstaked` even shares v0.2's exact `topic0` with a different layout. So
decoding is keyed **per pool** via `PoolConfig.events` (an `EventSpec` per
`topic0`), not a global topic map. `Migrated` (principal leaving v0.1 for v0.2)
is stored as its own `event_type` so the unwind stays legible.

Pool configs live in `DEFAULT_POOLS` in
`src/genkei/ingest/onchain_staking.py`.

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
  spec's event-data word (word 0 for v0.2, word 1 for v0.1) and divides by
  10^18 before writing. Query formulas should use `amount_token` directly with
  an `event_type` `CASE`, without an extra `/ 1e18` conversion.
- **Principal vs. token balance** — `SUM(staked − unstaked − migrated −
  operator_removed − slashed)` gives active *principal*, which is below the
  contract's on-chain LINK balance by the residual reward reserve (funded
  outside the principal events). Verified on v0.1 (2026-07-15): ~161k LINK
  principal vs ~444k token balance; the ~283k gap is the reward reserve, not a
  decode error. Reconcile on principal, not token balance.
- **v0.1 `migrated` events are the v0.1→v0.2 migration record** — of ~24.05M
  LINK ever staked into v0.1, ~23.82M migrated to v0.2 (7,642 `migrated`
  events), leaving ~161k principal during the tail of the unwind.

## How it runs

- **Manual / on-demand** —
  `python -m genkei.ingest.onchain_staking --backfill` for an initial
  backfill from each configured pool's deployment block, then `python -m
  genkei.ingest.onchain_staking` (no flag) for incremental
  resume-from-highest-stored-block. There is no per-run lower-bound
  override today; deployment blocks live in the collector config.
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

- ~~**B-116** — v0.1 legacy Staking contract~~ → shipped 2026-07-15 (see
  `docs/resolved.md`): per-pool `EventSpec` decoding + `chainlink-v01` in
  `DEFAULT_POOLS`, backfilled from block 16083969.
- **Lido / RocketPool / EigenLayer expansion** — same `DEFAULT_POOLS`
  config pattern; no schema migration.
- **Daily cron** — wire when a research session asks for steady-state
  staking-flow signals.
- **`genkei staking` typed CLI subcommand** — today's path is
  `genkei query`.
