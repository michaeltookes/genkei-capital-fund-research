# Spot crypto ETF net flow — data-source investigation (B-107)

**Status:** Phase 1 investigation complete (2026-06-07). Recommendation: **proceed to v2 implementation scoped to iShares (IBIT + ETHA + ETHB)**. Multi-issuer expansion deferred to v2.1.

**Context:** B-105 v1 shipped "daily-dollar-volume per asset" via Yahoo OHLCV as a magnitude proxy for institutional ETF activity. The 2026-06-02 ETH / SOL / SUI research sessions explicitly named *signed net flow* (creations vs redemptions) as the canonical missing institutional-flow signal. B-107 pursues that signal via primary or near-primary sources, since the third-party paths (Farside, SoSoValue) are Cloudflare-walled and Yahoo `quoteSummary` is auth-gated.

## Findings

### Path 1 — SEC EDGAR daily filings: **DEAD END**

The original B-107 spec assumed some SEC form carried daily creation/redemption baskets or daily shares-outstanding for spot ETFs. Pulling IBIT's complete filing index from EDGAR (`https://data.sec.gov/submissions/CIK0001980994.json`) shows the actual cadence:

| Form | Frequency | Contains shares-outstanding? |
|---|---|---|
| 10-Q | Quarterly | Yes, point-in-time at quarter end |
| 10-K | Annual | Yes, point-in-time at year end |
| 8-K | Event-driven (~monthly) | No — material events only |
| FWP | Marketing | No — free writing prospectus |
| 424B3 / 424I / POS AM / S-1/A | Prospectus / registration amendments | No — disclosure documents |

**No daily filings.** Spot crypto ETFs are grantor trusts (commodity pools, SIC 6221), NOT registered investment companies — so N-CEN / N-PORT (the daily-ish forms for mutual funds and ETFs registered under the 1940 Act) do not apply. The spec's N-CEN / N-PORT / Form 8937 / Form NPX candidates are all either annual or inapplicable.

10-Q gives 4 quarter-end checkpoints per year per ETF, which is too coarse for the institutional-flow signal we want. Useful as a *cross-check* against issuer-published numbers (B-110-style triangulation) but not the primary data path.

### Path 2 — Issuer product pages: **MIXED, iShares wins**

HEAD/GET probes against each major issuer's product page (Mozilla User-Agent, no auth):

| Issuer | ETF | Probe result | Verdict |
|---|---|---|---|
| iShares | IBIT, ETHA, ETHB | HTTP 200, no Cloudflare, public JSON feed exists | **WINNER** — see Path 2a |
| Bitwise | BITB | HTTP 200, no Cloudflare, "shares outstanding" in HTML | Worth follow-up for v2.1 |
| Fidelity | FBTC | HTTP 404 on guessed URL; correct path not yet found | Deferred — needs URL spike |
| Grayscale | GBTC | HTTP 429 (rate-limited even on first request) | Deferred — likely needs auth or distributed crawl |
| ARK / 21Shares | ARKB | HTTP 403, 4 Cloudflare markers | Cloudflare-walled, same problem as Farside |

#### Path 2a — iShares public product-screener JSON: **THE WINNING PATH**

iShares publishes a single public JSON feed covering all ~530 of their US ETFs:

```
https://www.ishares.com/us/product-screener/product-screener-v3.1.jsn
  ?dcrPath=/templatedata/config/product-screener-v3/data/en/us-ishares/ishares-product-screener-backend-config
  &siteEntryPassthrough=true
  &loc=en_us
```

Returns a ~1.9 MB JSON object keyed by `portfolioId`. No auth, no Cloudflare, no rate limit observed. Includes all three of iShares' crypto-relevant products:

| key (portfolioId) | ticker | fundName | navAmount | totalNetAssets | asOf |
|---|---|---|---|---|---|
| 333011 | IBIT | iShares Bitcoin Trust ETF | $33.81 | $46,211,335,562 | Jun 05, 2026 |
| 337614 | ETHA | iShares Ethereum Trust ETF | $11.75 | $4,450,501,503 | Jun 05, 2026 |
| 348532 | ETHB | iShares Staked Ethereum Trust ETF | $20.03 | $458,312,812 | Jun 05, 2026 |

**Derivation:**

```
shares_outstanding = totalNetAssets / navAmount
net_flow_usd_day  = (shares_outstanding_today − shares_outstanding_yesterday) × close_price
```

For IBIT on 2026-06-05: shares_outstanding = 46_211_335_562 / 33.81 ≈ **1.367 billion shares**. Match against IBIT's market cap and the math is consistent.

**Update cadence:** the `navAmountAsOf` and `totalNetAssetsFundAsOf` fields are both stamped `Jun 05, 2026` — yesterday's close. Confirmed daily, T+1.

**Per-fund field count:** 87 fields including aladdinAssetClass, CUSIP, ISIN, all NAV-history annualized returns, inception date, expense ratio, etc. Far richer than needed but no harm in storing the whole blob.

### Path 3 — Coinbase institutional product feed: **NOT INVESTIGATED**

The spec mentioned this as a possible alternative for ETF creation/redemption data. Skipped this investigation given that Path 2a closes the v1 signal for the dominant issuer; revisit only if multi-issuer expansion stalls in v2.1.

## Recommendation — v2 scope

**Proceed to v2 implementation, scoped to iShares only for v1.**

Rationale:
- iShares IBIT alone is the dominant spot BTC ETF by AUM ($46.2B as of 2026-06-05). Single-issuer coverage of IBIT + ETHA captures most of the institutional-flow signal the research sessions named.
- The feed is single-request, no-auth, free, and covers three crypto products simultaneously. Operationally cheap.
- Backfill is naturally daily-forward only from this endpoint — but the v2 signal is *forward-going* institutional flow, not historical. Daily history is a nice-to-have, not a v1 requirement.
- Multi-issuer expansion (FBTC, GBTC, BITB, ARKB) and historical backfill via 10-Q quarter-end shares-outstanding are clear v2.1 follow-ups, filed separately.

## v1 acceptance criteria (revised from B-107 spec)

- New ingester `genkei.ingest.ishares_etf_flows` reading the product-screener JSON above.
- Lands raw blob in `meta.raw_blobs` per ingest run (one JSON blob covering all iShares products in a single fetch).
- Normalizes the three crypto products into a new table `etf.fund_snapshots` keyed on `(ticker, snapshot_date)`:
  - `ticker` (IBIT / ETHA / ETHB), `cik`, `cusip`, `isin`
  - `snapshot_date` (= `navAmountAsOf`)
  - `nav_per_share_usd` (`navAmount`)
  - `total_net_assets_usd` (`totalNetAssets`)
  - `shares_outstanding` (derived: `total_net_assets / nav_per_share`)
  - `daily_net_flow_usd` (derived: `(shares_today − shares_yesterday) × close_today`, computed on insert via `LAG()` window in the normalizer)
  - `ingest_run_id` for audit
- Daily cron in a new `ishares-daily.yml` workflow at ~22:00 UTC (after iShares publishes the T+1 NAV / TNA snapshot, before next-day open).
- `genkei watchlist health` monitors the source for staleness.
- New typed CLI subcommand `genkei etf-flows --asset BTC --since 2026-06-07` returning the snapshot rows + derived net flow. Aliases `--asset ETH` for ETHA and ETHB.
- Unit tests pin the extractor for the JSON-keyed payload (one record per crypto-relevant key) and the shares-outstanding derivation arithmetic.

## Deferred to v2.1 (filed as separate backlog items if pursued)

1. **Multi-issuer expansion** — FBTC (Fidelity), BITB (Bitwise), GBTC (Grayscale), ARKB (ARK). Each needs its own URL discovery + Cloudflare evaluation. ARKB confirmed walled; GBTC rate-limited; FBTC and BITB plausible.
2. **Historical backfill** — quarterly checkpoints via SEC 10-Q shares-outstanding extraction. Useful for triangulation against the daily feed once running.
3. **Reconciliation against Yahoo dollar-volume (B-105 v1)** — sanity-check that daily net flow direction matches the volume proxy.
4. **Coinbase institutional product feed** — skipped in Phase 1; revisit if needed.
