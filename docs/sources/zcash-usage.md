# Zcash on-chain usage / shielded-pool data — source spike (2026-07-07)

The load-bearing data gap from the 2026-07-06 ZEC research decision
(`docs/research/decisions/2026-07-06-zcash-privacy-thesis-assessment.md`): the
lake had ZEC **price** but no **usage** signal, so the entire privacy-*adoption*
thesis was unmeasurable — every ZEC decision was graded on price momentum alone,
which cannot distinguish "the privacy narrative is becoming real adoption" from
"a momentum pump on a story." This documents the free-source survey and the
go/no-go.

## Verdict: **GO** — scoped to a forward-only shielded-pool snapshot

The single most important privacy metric — the **shielded share of supply** — is
available free/keyless. The trade-off is that it's a *current snapshot* (no deep
history, no transaction-level flow) from a single explorer.

## The winning source — zcashexplorer.app `blockchain-info`

`GET https://mainnet.zcashexplorer.app/api/v1/blockchain-info` (keyless, no
Cloudflare, verified 2026-07-07) returns the Zcash node's `getblockchaininfo`,
whose `valuePools` array is the canonical shielded-pool measure — the on-chain
`chainValue` held in each pool:

| pool | chainValue (ZEC, 2026-07-07) | meaning |
|---|---|---|
| transparent | 12,349,120 | not private (t-addresses) |
| sprout | 25,409 | legacy shielded pool (deprecated) |
| sapling | 620,198 | shielded (2018 upgrade) |
| **orchard** | **3,765,576** | shielded (2022 NU5; the modern default) |
| lockbox | 48,330 | dev-fund lockbox (NU6) |
| **total shielded** (sprout+sapling+orchard) | **4,459,513** | **≈ 26.5% of supply** |

This directly quantifies the decision's claim that "most ZEC usage has
historically been transparent": **73.5% transparent vs 26.5% shielded**, and the
bulk of shielded value sits in **Orchard** (the newest, strongest-privacy pool)
— evidence that shielded adoption *has* been migrating to the modern pool. The
headline signal for the thesis is the **shielded-share trend over time**: is
26.5% growing (privacy adoption real) or flat/shrinking (narrative-only)?

## Proposed ingester shape (if built)

A **forward-only daily snapshot**, structurally identical to the iShares/Bitwise
ETF-NAV ingesters (B-107/B-113):

- New `genkei.ingest.zcash_usage` collector: one GET to `blockchain-info`, land
  one row per `(pool, snapshot_date)` (or one wide row per `snapshot_date` with
  a column per pool) into a new `zcash.shielded_pools` table, with derived
  `shielded_total` + `shielded_share_pct`. Raw blob to `meta.raw_blobs`.
- Idempotent on `(pool, snapshot_date)`; daily cron; `genkei watchlist health`
  monitors it. A CLI surface (`genkei zcash-usage` or fold into `genkei prices`)
  returns the shielded-share series.
- The series builds forward from day 1 — so the value compounds the longer it
  runs (the whole point is the *trend*).

## Coverage limits (documented honestly)

- **No historical backfill.** The explorer exposes only the current
  `blockchain-info`; every block-level / historical endpoint 404s. Deep history
  would require a **full Zcash node** (walk `getblockchaininfo` / `z_gettreestate`
  at past block heights — heavy infra) or a paid API. So the series starts the
  day collection begins. This is the same forward-only constraint the ETF NAV
  ingesters accept — fine for a *trend* signal, not for backtesting deep history.
- **Stock, not flow.** `valuePools` is the *cumulative* ZEC in each pool (the
  stock). It does **not** give the daily *shielded-transaction share* (the flow —
  what % of today's txns were shielded). The stock is the more important thesis
  metric (cumulative privacy adoption), but the flow would be a complementary
  add and needs a tx-level source (a node, or a richer explorer).
- **Single-source dependency.** One explorer (`mainnet.zcashexplorer.app`). If it
  goes dark, the fallback is a public/self-hosted Zcash node RPC
  (`getblockchaininfo` returns the same `valuePools`) — a heavier but canonical
  path. Worth pinning a second explorer as a soft fallback if one surfaces.

## Paths NOT taken (recorded for completeness)

- **Blockchair `zcash/stats`** — free + live, but general network stats only
  (tx count, fees, hashrate); **no** shielded/pool/privacy fields. Good for
  network-activity context, useless for the privacy split.
- **CoinMetrics community API** — 26 free ZEC metrics, **none** privacy-related;
  shielded metrics are Pro-tier (paid).
- **Messari free metrics** — endpoint returns 404 (deprecated free tier).
- **3xpl** — requires an access token (gated).
- **Full Zcash node** — the canonical, complete source (current + historical +
  tx-level), but running/maintaining a node is out of scope for a small
  speculative position; revisit only if ZEC's weight or the research cadence
  grows enough to justify the infra.

## Why this is worth building (even for a small position)

The ZEC position is a lottery ticket, but the whole reason it was added was to
*research the privacy thesis over time*. The shielded-share trend is the one
signal that would let a future `/research` session (or `/reflect-decisions`)
grade the thesis on **adoption**, not price — and it's the exact trigger the
2026-07-06 decision named for sizing up ("first real shielded-usage adoption
data"). A ~150-line forward-snapshot ingester on a free feed is proportionate to
that.
