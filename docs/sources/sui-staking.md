# Sui staking ingester (B-088)

Daily snapshot of every active Sui validator's stake, voting power,
commission rate, and APY. Sourced from two public mainnet JSON-RPC
calls. **Forward-only from the first run** — the public Sui fullnode
does not expose historical epoch state, so backfill is structurally
impossible without an indexer-side API.

## Coverage v1

Every active Sui mainnet validator (~129 as of 2026-06). Per-epoch
snapshot rows capturing:

- `staking_pool_sui_balance` (current stake, MIST)
- `pending_stake` / `pending_total_sui_withdraw`
- `voting_power`
- `commission_rate`
- `apy`

Validator metadata (name, description, project_url) is captured
alongside, deduped on `validator_address`.

## Endpoint contract

- **Base URL** — `https://fullnode.mainnet.sui.io` (public mainnet
  fullnode).
- **Auth** — none.
- **Rate limit** — undocumented; community ceiling ≈ 1-2 req/s.
  Collector caps at 1 req/s. Two POSTs per run.
- **Methods** —
  - `suix_getLatestSuiSystemState` — current epoch's full system
    state including every active validator.
  - `suix_getValidatorsApy` — per-validator APY array, joined to the
    validator list on `address` → `suiAddress`.
- **Response shape** — JSON-RPC `{jsonrpc, result, id}`.

## Schema

- `onchain.sui_validators` — fact, PK `(epoch, validator_address)`.
  Plain table — Sui's epoch cadence is ~24h and ~129 validators →
  ~47k rows/year, no hypertable needed. `epoch_start_ts` is the
  timestamp to use for charting and `--since` filters.

Stake amounts stored as **MIST** (Sui atomic unit, 1 SUI = 10⁹ MIST),
not converted to SUI at write time. Column type `NUMERIC(40, 0)` to
handle future inflation — Sui max supply (10B SUI = 10¹⁹ MIST) would
overflow `BIGINT`.

## v1 limitations & known issues

- **NO BACKFILL SUPPORT** — the public RPC only exposes the current
  epoch's system state. `suix_getEpochs` returns `Method not found`
  on the public fullnode (indexer-API-only). **Forward-only from day
  of first run.** Historical reconstruction deferred as a v2 follow-up
  pending an alternative data source.
- **Forward-only is idempotent within epoch** — re-runs within the
  same epoch are no-op upserts on the PK; safe to run multiple times
  per day during development.
- **Epoch-transition timing** — fixed daily cron at 06:00 UTC may miss
  mid-epoch transitions. Future improvement: trigger on
  epoch-change event instead of fixed cron.
- **Stake amounts in MIST, not SUI** — querying balance requires
  `amount / 1e9`. Documented on the column to avoid the "why is this
  10⁹× the expected number?" surprise.
- **No operator-level drill-down** — pending flows + commission live
  at the validator level today. Operator → validator → delegator
  hierarchy is collapsed.
- **Single-stage** — parse inline + upsert directly. No
  `meta.raw_blobs` hop.

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
3. **APY populated** — every row carries a non-null APY (the join to
   `suix_getValidatorsApy` is complete). Null APYs indicate the
   second RPC call failed silently.
4. **Stake delta sanity** — `SUM(staking_pool_sui_balance)` epoch
   over epoch should drift smoothly (< 5% delta) absent a major
   protocol event. Sudden jumps signal a parsing regression or
   genuine market event worth investigating.
5. **Idempotent within epoch** — two runs within the same epoch must
   produce identical row counts; the PK swallows duplicates.

## Follow-ups

- **Historical backfill via indexer API** — when a Sui indexer-side
  `getEpoch` becomes accessible (or a third-party publishes
  historical epoch state), wire it through to backfill from genesis.
- **Epoch-transition-triggered run** — replace the fixed cron with a
  trigger on epoch boundaries (would need a webhook / polling daemon).
- **Operator-level breakdown** — capture per-operator pending flows
  and commission events; would need a separate table since the
  current PK is at validator granularity.
- **`genkei sui` typed CLI** — today's path is `genkei query`.
- **Pair with B-089 SUI unlocks** — vesting unlocks + validator
  stake-delta give a clean picture of where unlocked SUI is going
  (staked, sold, idle).
