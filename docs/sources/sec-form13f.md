# SEC Form 13F (institutional holdings) ingester (B-080)

Form 13F is the SEC's quarterly disclosure of institutional investment
manager holdings — every fund with > $100M AUM reports their long-only
positions within 45 days of quarter-end. v1 fetches every uncached 13F
filing for every watchlist filer (Berkshire, BlackRock, Vanguard, etc.)
and decomposes the information-table XML into per-holding rows.

## Coverage v1

The watchlist's `filers:` section curates the universe. v1 covers ~10
filers spanning Buffett (Berkshire Hathaway), the top US asset managers
(BlackRock, Vanguard, State Street), and a few activist / concentrated
funds (Pershing Square, ValueAct, Greenlight).

Two filing shapes:

- **Holdings-bearing** — `13F-HR`, `13F-HR/A`, `13F-CTR`, `13F-CTR/A`.
  Carries the information-table XML; this ingester fetches + parses it.
- **Notice-only** — `13F-NT`, `13F-NT/A`. References another filing
  (rare; "we filed with someone else"). Marked processed without an
  XML fetch so the normalizer doesn't retry forever.

Steady state: ~150k holding rows across ~1k filings.

## Endpoint contract

- **Base URLs** — `https://data.sec.gov/submissions` (Phase A:
  per-filer submissions index) + `https://www.sec.gov/Archives/edgar/
  data` (Phase B: per-filing XML).
- **Auth** — none, but `SEC_USER_AGENT` fair-access (same as parent SEC).
- **Rate limit** — SEC's 10 req/s; collector caps at 8 req/s. Phase B
  is the volume driver — every uncached 13F-HR is two fetches
  (`index.json` to find the XML filename, then the XML itself).
- **Endpoints** —
  - Phase A: `/submissions/CIK{filer_cik}.json` + history pages.
  - Phase B: `/Archives/edgar/data/{cik}/{accession}/index.json` +
    `/Archives/edgar/data/{cik}/{accession}/{filename}.xml`.
- **Filename quirk** — the info-table XML filename varies per filing
  (`infotable.xml`, `informationtable.xml`, `form13fInfoTable.xml`, …).
  `index.json` is the source of truth.

## Schema

- `sec.filers` — entity dim, PK `filer_cik`. Zero-padded 10-char CIK
  matching the watchlist normalization.
- `sec.form13f_filings` — fact, PK `accession_number`. One row per
  13F filing (filer, period_of_report, form_type, total_value,
  total_holdings_count).
- `sec.form13f_holdings` — fact, PK `(accession_number, holding_idx)`.
  One row per holding within a filing: issuer name, CUSIP,
  `value_usd` (NUMERIC, dollars after ×1000 conversion at normalize
  time), shares, put/call/sh class, voting authority breakdown.
- `sec.form13f_normalized_filings` — marker tracking already-parsed
  accessions.

## v1 limitations & known issues

- **`value_usd` ×1000 conversion** — Form 13F publishes values in
  thousands of dollars per SEC convention. The normalizer multiplies
  by 1000 so the column unit is unambiguous USD; documented on the
  column.
- **CUSIP join key** — `sec.form13f_holdings.cusip` joins to the
  watchlist's sparse equity `cusip:` fields via `find_equity_by_cusip`
  / crowding helpers, not to `sec.companies` (that table is CIK-keyed
  and has no `cusip` column). Holdings for non-watchlist issuers carry
  CUSIP + issuer name only.
- **Two-phase fetch** — Phase A failure (submissions index) is hard
  (no filings to process). Phase B failure (per-filing XML) is soft
  (continue, log to `partial_endpoints`).
- **Incremental `--limit 50`** — caps Phase B at 50 uncached 13F-HRs
  per run. ~10 filers × quarterly cadence ≈ 2-3 new filings/day
  steady-state, so the limit only matters during initial backfill or
  after a missed run. `--backfill` removes the cap.
- **Notice-only handling** — `13F-NT` / `13F-NT/A` are marked
  processed without fetching XML. Without this guard the normalizer
  would retry every notice-only filing every run forever.
- **No cross-filing aggregation** — "what's Berkshire's current AAPL
  position?" requires a `MAX(period_of_report)` filter per
  `(filer_cik, cusip)`. Materialized views deferred.

## How it runs

- **Daily workflow** — `.github/workflows/sec-daily.yml`, cron
  `30 11 * * *` (11:30 UTC). The 13F collect + normalize steps run after
  the parent SEC and Form 4 steps; default incremental mode caps Phase B
  at 50 uncached holdings-bearing filings.
- **Manual run** — `python -m genkei.ingest.sec_form13f`
  (incremental, 50 newest) or `--backfill` (no limit).

## Query path

`genkei query` over `sec.form13f_*`. A typed
`genkei filings --filer "Berkshire Hathaway" --form 13F-HR` resolves
to `sec.form13f_filings`. Crowding analysis queries (B-061) join
`form13f_holdings` against the watchlist via CUSIP.

## Acceptance gates

Before consuming Form 13F signals:

1. **Upstream Phase A completed** — `meta.ingest_runs.metadata` for
   the latest `(sec_form13f, collect)` row records every watchlist
   filer's submissions index fetch.
2. **`sec.form13f_normalized_filings` keeping up** —
   `SELECT COUNT(*) FROM sec.form13f_filings f WHERE f.form_type IN
   ('13F-HR', '13F-HR/A') AND NOT EXISTS (SELECT 1 FROM
   sec.form13f_normalized_filings n WHERE n.accession_number =
   f.accession_number)` trends toward zero.
3. **No filer dominating the partial-endpoint log** — Phase B soft
   failures on one filer accounting for >50% of failures signals a
   filer-specific index-shape drift.
4. **`value_usd` units are dollars** — for the latest quarter's
   Berkshire AAPL holding, value_usd should match the SEC's
   publicly-stated AAPL position size (×1000 conversion catches the
   common bug if it ever silently drops).
5. **Notice-only filings marked processed** — `SELECT COUNT(*) FROM
   sec.form13f_filings f LEFT JOIN sec.form13f_normalized_filings n
   USING (accession_number) WHERE f.form_type LIKE '13F-NT%' AND
   n.accession_number IS NULL` returns zero (no XML fetch but marker
   present).

## Follow-ups

- **CUSIP coverage expansion** — keep the watchlist's sparse `cusip:`
  fields filled for equities that should participate in 13F crowding
  queries and emitters.
- **Materialized "current positions" view** — `(filer_cik, cusip)` →
  `MAX(period_of_report)` projection so queries don't need the
  per-query subquery.
- **Aggregate crowding score (B-061)** — already filed as a follow-up;
  this ingester is the load-bearing input.
- **Quarter-over-quarter delta view** — derived view computing
  Δ shares between consecutive quarters per `(filer, cusip)` for fast
  buy/sell-cluster detection.
