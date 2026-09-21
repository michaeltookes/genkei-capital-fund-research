# Sui staking ingester (B-088; GraphQL since B-145)

Daily snapshot of every active Sui validator's stake, voting power,
and commission rate. Sourced from the public Sui mainnet **GraphQL
API**. **Forward-only from the first run** — the API only exposes the
current epoch's validator state at this query path, so backfill is
structurally impossible without an indexer-side data source.

## Source history (read this before touching the collector)

- **v1 (B-088, 2026-06-07 → 2026-07-27)** — two JSON-RPC calls against
  the public fullnode (`suix_getLatestSuiSystemState` +
  `suix_getValidatorsApy`).
- **2026-07-27** — last successful JSON-RPC run (epoch 1202). Sui
  deprecated JSON-RPC on public fullnodes upstream; subsequent runs
  failed with `-32601 Method not found` plus an explicit migration
  notice pointing at gRPC/GraphQL.
- **2026-09-18 (B-145)** — collector ported to the GraphQL API.
  **Epochs 1203–1254 (2026-07-28 → 2026-09-17) are a permanent gap**
  in `onchain.sui_validators` — unbackfillable at this layer, same
  forward-only constraint as v1. Coverage resumed at epoch 1255.
- **APY is gone as of the port.** `suix_getValidatorsApy` was an
  RPC-computed convenience with no GraphQL equivalent (deriving APY
  from `staking_pool.exchange_rates` dynamic fields is possible but
  heavy). Rows from epoch 1255 onward carry `apy = NULL`; pre-gap rows
  keep their stored APYs and the upsert never nulls an existing APY.

## Coverage

Every active Sui mainnet validator (~127 as of 2026-09). Per-epoch
snapshot rows capturing:

- `stake_amount_mist` (current stake, MIST)
- `pending_stake_mist` / `pending_withdraw_mist`
- `voting_power`
- `commission_rate`
- `apy` — **NULL since epoch 1255** (see history above)

Validator metadata (name) is captured alongside, deduped on
`validator_address`.

## Endpoint contract

- **URL** — `https://graphql.mainnet.sui.io/graphql` (public mainnet
  GraphQL API).
- **Auth** — none.
- **Rate limit** — undocumented. Collector caps at 1 req/s;
  ~3 POSTs per run (50-validator pages).
- **Query** — `epoch { epochId startTimestamp validatorSet {
  activeValidators(first, after) { pageInfo nodes { contents { json
  } } } } }`. Each node's `contents.json` is the on-chain
  `ValidatorV1` Move struct: **snake_case** field names, u64s as JSON
  strings, staking-pool figures nested under `staking_pool`.
- **Pagination** — cursor-based, 50 nodes/page. The collector aborts
  loudly if `epochId` changes between pages (epoch boundary mid-fetch)
  rather than stitching two epochs into one snapshot; the next daily
  run lands the new epoch cleanly.
- **Response shape** — GraphQL `{data, errors}`.

## Schema

- `onchain.sui_validators` — fact, PK `(epoch, validator_address)`.
  Plain table — Sui's epoch cadence is ~24h and ~127 validators →
  ~47k rows/year, no hypertable needed. `epoch_start_ts` is the
  timestamp to use for charting and `--since` filters.

Stake amounts stored as **MIST** (Sui atomic unit, 1 SUI = 10⁹ MIST),
not converted to SUI at write time. Column type `NUMERIC(40, 0)` to
handle future inflation — Sui max supply (10B SUI = 10¹⁹ MIST) would
overflow `BIGINT`.

## Limitations & known issues

- **NO BACKFILL SUPPORT** — the GraphQL `epoch` query serves current
  state; historical epoch reconstruction still needs an indexer-side
  source. **Forward-only**, with the 1203–1254 gap permanent.
- **Forward-only is idempotent within epoch** — re-runs within the
  same epoch are no-op upserts on the PK; safe to run multiple times
  per day during development.
- **Epoch-transition timing** — fixed daily cron at 06:00 UTC may miss
  mid-epoch transitions. Future improvement: trigger on
  epoch-change event instead of fixed cron.
- **Stake amounts in MIST, not SUI** — querying balance requires
  `stake_amount_mist / 1e9`. Documented on the column to avoid the
  "why is this 10⁹× the expected number?" surprise.
- **No operator-level drill-down** — pending flows + commission live
  at the validator level today. Operator → validator → delegator
  hierarchy is collapsed.
- **Raw blobs per page** — each GraphQL page's `data` payload is
  stored in `meta.raw_blobs` as `active_validators_page_<n>`.

## How it runs

- **Daily workflow** — `.github/workflows/sui-staking-daily.yml`, cron
  `0 6 * * *` (06:00 UTC). The earliest pull in the daily ingest
  train — Sui's mainnet epoch transitions cluster around early UTC.
- **Reads** — `GENKEI_DATABASE_URL`. No API key gate.
- **Manual run** — `python -m genkei.ingest.sui_staking`.

## Query path

`genkei query` over `onchain.sui_validators`. A typed `genkei sui
--validator <address> --since 2024-01-01` is a natural future
addition; it should filter on `epoch_start_ts` and order by `(epoch,
validator_address)` because the table is epoch-keyed rather than
`ts`-keyed.

## Acceptance gates

Before consuming Sui staking signals:

1. **Freshness** — `meta.ingest_runs.finished_at` for the latest
   `(sui_staking, collect)` row is within 36 hours.
2. **Validator count band** — `SELECT COUNT(DISTINCT validator_address)
   FROM onchain.sui_validators WHERE epoch = (SELECT MAX(epoch) FROM
   onchain.sui_validators)` is between 100 and 200. Outside the band
   signals a network event or a parser regression.
3. **APY expectations by era** — rows up to epoch 1202 carry APYs;
   rows from epoch 1255 onward are `apy = NULL` by design. A non-null
   APY on a post-port epoch indicates something is rewriting history.
4. **Stake delta sanity** — `SUM(stake_amount_mist)` epoch
   over epoch should drift smoothly (< 5% delta) absent a major
   protocol event. Sudden jumps signal a parsing regression or
   genuine market event worth investigating. (Across the 1202 → 1255
   gap the delta is a legitimate −1.8%: 7.16B → 7.03B SUI staked.)
5. **Idempotent within epoch** — two runs within the same epoch must
   produce identical row counts; the PK swallows duplicates.

## Follow-ups

- **APY restoration** — either derive from
  `staking_pool.exchange_rates` dynamic fields (epoch-over-epoch pool
  token exchange-rate growth) or adopt a gRPC/indexer source that
  publishes validator APY directly.
- **Historical backfill via indexer API** — when a Sui indexer
  publishes historical epoch state, wire it through to backfill the
  gap and pre-history.
- **Epoch-transition-triggered run** — replace the fixed cron with a
  trigger on epoch boundaries (would need a webhook / polling daemon).
- **Operator-level breakdown** — capture per-operator pending flows
  and commission events; would need a separate table since the
  current PK is at validator granularity.
- **`genkei sui` typed CLI** — today's path is `genkei query`.
- **Pair with B-089 SUI unlocks** — vesting unlocks + validator
  stake-delta give a clean picture of where unlocked SUI is going
  (staked, sold, idle).
