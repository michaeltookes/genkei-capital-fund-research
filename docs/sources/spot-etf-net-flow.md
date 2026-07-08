# Spot crypto ETF net flow — data-source investigation (B-107)

**Status:** Phase 1 investigation complete (2026-06-07); v2 shipped iShares (IBIT + ETHA + ETHB, B-107); **v2.1 added Bitwise BITB (B-113, 2026-06-30)** — see "B-113 — Bitwise expansion" below. Remaining issuers (FBTC / GBTC / ARKB) stay deferred behind their access walls.

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

```text
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

```text
shares_outstanding = totalNetAssets / navAmount
net_flow_usd_day  = (shares_outstanding_today - shares_outstanding_yesterday) x close_price
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

- New ingester `genkei.ingest.ishares` reading the product-screener JSON above.
- Lands raw blob in `meta.raw_blobs` per ingest run (one JSON blob covering all iShares products in a single fetch).
- Normalizes the three crypto products into a new table `etf.fund_snapshots` keyed on `(ticker, snapshot_date)`:
  - `ticker` (IBIT / ETHA / ETHB), `cusip`, `isin`
  - `snapshot_date` (= matching `navAmountAsOf` and `totalNetAssetsFundAsOf`)
  - `nav_per_share_usd` (`navAmount`)
  - `total_net_assets_usd` (`totalNetAssets`)
  - `shares_outstanding` (derived: `total_net_assets / nav_per_share`)
  - `ingest_run_id` for audit
- Daily net flow is derived at query time in `src/genkei/cli/etf_flows.py` as `(shares_today - shares_yesterday) x nav_today` via `LAG()`; it is not stored in `etf.fund_snapshots`.
- Daily cron in `.github/workflows/ishares-daily.yml` at `0 13 * * *` (after iShares publishes the T+1 NAV / TNA snapshot, before next-day open).
- `genkei watchlist health` monitors the source for staleness.
- New typed CLI subcommand `genkei etf-flows --asset BTC --since 2026-06-07` returning the snapshot rows + derived net flow. Aliases `--asset ETH` for ETHA and ETHB.
- Unit tests pin the extractor for the JSON-keyed payload (one record per crypto-relevant key) and the shares-outstanding derivation arithmetic.

## B-113 — Bitwise expansion (2026-06-30)

The second issuer, landed as the concrete use-case raised by the 2026-06-30
BTC research decision (which named the stale, volume-proxy ETF-flow signal as
the single highest-value add to sharpen the BTC confirmation trigger).

**Source — Bitwise product page HTML (free, no auth, no Cloudflare).** Unlike
iShares' single JSON feed, Bitwise serves each fund on its own
statically-generated (Next.js) product site. BITB lives at **`bitbetf.com`**;
the fund financials are **server-rendered into the page HTML** — there is no
public JSON API behind it (the only client-side calls are a Salesforce contact
form + a Turnstile widget, neither carrying fund data). Verified live
2026-06-30.

| field | value (2026-06-28 strike) | source on page |
|---|---|---|
| Shares Outstanding | 66,690,000 | "Key Facts" grid |
| Net Assets (AUM) | $2,181,609,770 | "Key Facts" grid |
| NAV / share | $32.71 | "NAV and Market Price" block |
| CUSIP / ISIN | 09174C104 / US09174C1045 | "Key Facts" grid |
| as-of date | 06/28/2026 | "Data as of" in the NAV block |

**Extractor design** (`src/genkei/ingest/bitwise.py`): every field is anchored
on the **label text** (e.g. `Shares Outstanding`), never the build-generated
`c-*` CSS class names — those churn on every Bitwise site rebuild, so a
class-keyed parser would silently break. Bitwise publishes NAV, net assets,
AND shares outstanding *independently* (iShares only publishes NAV + TNA, and
we derive shares), so all three are stored as published and gated on **mutual
reconciliation** — `nav × shares` must agree with `net_assets` within 2% (the
analog of iShares' "navAmountAsOf must equal totalNetAssetsFundAsOf" check;
the page stamps only the NAV strike date inline). Observed gap ≈ 0.01%.

**No query change needed.** `genkei etf-flows --net-flow` already filters
`etf.fund_snapshots` by underlying asset + watchlist ticker (not issuer), so
BITB surfaces alongside the iShares rows automatically. Daily T+1/T+2 cron in
`bitwise-daily.yml`, staggered 30 min behind `ishares-daily.yml`.

**ETHW added (B-129, 2026-07-07).** Bitwise's Ethereum ETF is now pinned in
`PRODUCT_URLS` at `https://ethwetf.com/` (same Next.js page shape, same
labels) — the first non-BlackRock issuer on the *ETH* net-flow surface
(alongside iShares ETHA/ETHB). One wrinkle it surfaced: ETHW publishes the NAV
strike and the Fund Details (shares/AUM) section a day apart (NAV T+2, AUM
T+1), where BITB stamps both the same day. The parser's strict date-equality
would have silently dropped every skewed day, so it was relaxed to a bounded
skew (`MAX_SECTION_DATE_SKEW_DAYS = 3`) with the `nav × shares ≈ net_assets`
reconciliation as the real coherence gate, and the row is now dated by the
Fund Details section (where shares — the net-flow driver — lives). BITB, whose
sections share a date, is unaffected (skew 0).

## Deferred (filed as separate backlog items if pursued)

1. **Remaining issuers** — FBTC (Fidelity), GBTC (Grayscale), ARKB (ARK).
   ARKB confirmed Cloudflare-walled; GBTC rate-limited; FBTC URL not yet
   found. (Bitwise ETHW shipped 2026-07-07, B-129 — see above.)
2. **Historical backfill** — quarterly checkpoints via SEC 10-Q shares-outstanding extraction. Useful for triangulation against the daily feed once running.
3. **Reconciliation against Yahoo dollar-volume (B-105 v1)** — sanity-check that daily net flow direction matches the volume proxy.
4. **Coinbase institutional product feed** — skipped in Phase 1; revisit if needed.
