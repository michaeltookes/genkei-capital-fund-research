# SEC EDGAR ingester (B-027)

SEC EDGAR is the **regulatory disclosure spine** of the equity side of
the lake. v1 covers the two endpoints downstream issuer-level SEC
ingesters build on top of: the per-issuer submissions index and the
XBRL company-facts dataset.

## Coverage v1

For every equity in the watchlist with a non-null `cik`:

- **Submissions index** — every filing the issuer has ever made
  (10-K, 10-Q, 8-K, Form 4, Form 13F, S-1, …) with form type,
  accession number, filing date, period of report, primary document.
- **XBRL company-facts** — every numeric fact the issuer has tagged in
  the standard XBRL taxonomy (us-gaap, dei, …) across every filing,
  with unit, period start/end, and accession.

Form 4 (insider transactions) lives in its own ingester (see
`docs/sources/sec-form4.md`) and consumes `sec.filings` as its
discovery index. Form 13F (institutional holdings) lives in another
(`docs/sources/sec-form13f.md`) and reads the watchlist `filers:`
universe directly from SEC submissions indexes because 13F filers are
investment managers, not necessarily issuer CIKs in `sec.filings`.

## Endpoint contract

- **Base URLs** — `https://data.sec.gov/submissions` (submissions) and
  `https://data.sec.gov/api/xbrl/companyfacts` (XBRL facts).
- **Auth** — none, but SEC's fair-access rule (G-022) requires a
  meaningful User-Agent identifying the requester. Set
  `SEC_USER_AGENT` (real name + contact email). The collector defaults
  to a project placeholder which SEC may rate-limit or reject.
- **Rate limit** — documented 10 req/s across all data.sec.gov; the
  collector caps at 8 req/s for headroom.
- **Endpoints** — `/submissions/CIK{cik}.json` + history pages
  (`/submissions/CIK{cik}-submissions-001.json` etc.),
  `/api/xbrl/companyfacts/CIK{cik}.json`.

## Schema

- `sec.companies` — entity dim, PK `cik`. Issuer metadata
  (name, ticker, sic, fiscal year end). CUSIPs are not stored here;
  13F CUSIP scoping lives in the watchlist's sparse equity `cusip:`
  fields.
- `sec.filings` — fact, PK `accession_number`. One row per filing:
  form type, filing date, primary document, period of report.
  Discovery index for every downstream SEC ingester.
- `sec.facts` — XBRL fact table, PK
  `(cik, concept, unit, period_start, period_end, accession_number)`.
  Hypertable on `period_end`, 30-day chunks, compressed > 30d
  (PK shape fixed in 20260606 migration).

## v1 limitations & known issues

- **`SEC_USER_AGENT` is load-bearing** — SEC explicitly rejects
  bot-flavored UAs. Set the env var to "Real Name email@example.com"
  format. Missing or placeholder UA → soft-block from SEC after a few
  hundred requests.
- **Single-mode design** — submissions returns recent filings +
  history-page pointers; XBRL returns full history per call. Daily
  and backfill are the same code path; PK upserts handle revisions.
- **200-OK-with-error envelope** — bad CIK returns HTTP 200 with
  `{"Error": …}`. The collector detects and records as partial
  failure (G-020 fix).
- **Watchlist-scoped, not universal** — only issuers with a `cik` in
  the watchlist's `equities:` section are fetched. Adding coverage
  for a new ticker = filling the `cik` field, no code change.
- **Submissions history page count** — large issuers (Berkshire,
  Apple) have thousands of filings spread across several history
  pages. The collector walks every page; an upstream pagination shape
  change would silently truncate coverage.

## How it runs

- **Daily workflow** — `.github/workflows/sec-daily.yml`, cron
  `30 11 * * *` (11:30 UTC). Sequenced after FRED.
- **Two-stage** — collect → `meta.raw_blobs` (one blob per CIK per
  endpoint) → normalize → `sec.companies` / `sec.filings` / `sec.facts`.
- **Reads** — `SEC_USER_AGENT` + `GENKEI_DATABASE_URL`.
- **Backfill** — single-mode; no `--backfill` flag.

## Query path

`genkei query` over `sec.*`. The `genkei filings --ticker AAPL --form
10-K` typed surface resolves to `sec.filings`. `genkei filings --concept
us-gaap:Revenues --ticker AAPL` resolves to `sec.facts`.

## Acceptance gates

Before consuming SEC-derived signals:

1. **Freshness** — `meta.ingest_runs.finished_at` for the latest
   `(sec, collect)` + `(sec, normalize)` rows is within 36 hours.
2. **Every watchlist issuer with a `cik` covered** — `SELECT DISTINCT
   cik FROM sec.companies` matches the watchlist's `cik`-bearing
   `equities:` set.
3. **Latest filing per issuer within 90 days** — most issuers file at
   least quarterly. Longer gaps signal a stale CIK / merger / delist;
   investigate manually.
4. **No partial-endpoint failures** — `metadata.partial_endpoints` is
   empty.
5. **`SEC_USER_AGENT` not the project default** — `meta.ingest_runs.
   metadata.user_agent` (when present) is set to a real name + email.
6. **XBRL fact PK uniqueness** — `SELECT cik, concept, unit, period_start,
   period_end, accession_number, COUNT(*) FROM sec.facts GROUP BY 1,2,3,4,5,6
   HAVING COUNT(*) > 1` returns zero rows (PK enforces; regressions
   surface here).

## Follow-ups

- **`genkei sec` CLI subcommand** — typed query path covering
  filings + facts; today the path is mostly `genkei filings` + raw
  SQL.
- **8-K event-study integration** — `sec.filings` already captures
  8-Ks; a parallel parser would extract the 8.01 / 7.01 / 5.02 item
  type into a structured event table (B-094 surfaced this).
- **Watchlist CIK auto-discovery** — given a ticker, look up the CIK
  via the SEC ticker-to-CIK mapping endpoint instead of requiring
  manual `cik:` entries.
- **Comprehensive issuer universe** — today's watchlist is research-
  scoped (~12 names); a Russell 3000 expansion would need an
  efficient collector rewrite to stay under the 8 req/s budget.
