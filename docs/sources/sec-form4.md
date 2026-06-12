# SEC Form 4 (insider transactions) ingester (B-079)

Form 4 is the SEC's structured XML disclosure of every insider
transaction — buys, sales, option exercises, tax-withholding sales,
gifts. v1 fetches every uncached 4 / 4/A for every watchlist issuer
discovered in `sec.filings` and decomposes the XML into per-transaction
rows.

## Coverage v1

Every Form 4 / Form 4/A filing for every issuer with a CIK in the
watchlist's `equities:` section. Discovery happens upstream in the
SEC submissions ingester (`docs/sources/sec.md`): `sec.filings` is the
source-of-truth list; this ingester reads from it.

Steady state: ~50-100k transaction rows. Daily incremental volume is a
few dozen filings on a typical day, spiking around 10-Q release windows
when officers' trading windows reopen.

## Endpoint contract

- **Base URL** — `https://www.sec.gov/Archives/edgar/data` (public
  Archives host, distinct from `data.sec.gov`).
- **Auth** — none, but shares the parent SEC ingester's
  `SEC_USER_AGENT` fair-access requirement.
- **Rate limit** — same SEC 10 req/s ceiling; collector caps at
  8 req/s and shares the `HttpClient` instance with the parent SEC
  collector when run in the same process.
- **Endpoint** — per-filing XML at
  `/Archives/edgar/data/{cik}/{accession}/{filename}.xml`. The filename
  comes from `sec.filings.primary_document`, with any `xsl*/` prefix
  stripped to recover the raw XML pointer.

## Schema

- `sec.insiders` — entity dim, PK `reporter_cik`. One row per
  insider person (officer, director, 10%-owner).
- `sec.form4_transactions` — fact, PK `(accession_number, transaction_idx)`.
  Per-transaction row with reporter, issuer, transaction code, security
  type, shares, price, post-transaction holdings.
- `sec.form4_normalized_filings` — marker table tracking already-parsed
  accessions so re-runs don't reprocess every Form 4 from scratch.

Transaction codes (canonical single-letter SEC codes, stored as TEXT):

- `P` — open-market purchase
- `S` — open-market sale
- `A` — award / grant
- `F` — tax-withholding sale (mechanical, not discretionary)
- `M` — option exercise
- `G` — gift
- `J` — other (catch-all)

## v1 limitations & known issues

- **Two-phase fetch** — collector first reads from `sec.filings` to
  find uncached 4 / 4/A accessions, then fetches each XML from the
  Archives host. A run with no upstream `sec.filings` data does
  nothing.
- **Filename munging** — `primary_document` sometimes carries an
  `xsl*/` prefix pointing at a stylesheet wrapper instead of the raw
  XML. The collector strips it; an upstream change in the prefix
  scheme would silently break parsing.
- **Soft per-filing failure** — a 404 or malformed XML records the
  filing as failed in `meta.ingest_runs.metadata.partial_endpoints`
  but the run continues. Hard failures only on `sec.filings` discovery.
- **Incremental by default** — `--limit 200` caps each run at 200
  newest uncached filings (~25s @ 8 req/s). Backfill drops the limit.
- **No Form 3 / Form 5** — Form 3 (initial ownership) and Form 5
  (annual reconciliation) are deferred. They use the same Archives
  endpoint shape; adding either is mechanical.
- **Multi-class tickers** — GOOG + GOOGL file separately; this is
  intentional pass-through, no dedup. Downstream queries that want
  "Alphabet insider activity" need to union both CIKs.

## How it runs

- **Daily workflow** — `.github/workflows/sec-daily.yml`, cron
  `30 11 * * *` (11:30 UTC). Form 4 collect + normalize runs after the
  parent SEC normalize step so new `sec.filings` rows are visible before
  the Form 4 collector selects uncached accessions.
- **Manual run** — `python -m genkei.ingest.sec_form4` (incremental,
  200 newest) or `--backfill` (no limit).

## Query path

`genkei insiders --ticker AAPL --since 2024-01-01` typed surface.
`genkei insider-clusters --since 2024-01-01 --min-reporters 3` for
the cross-issuer cluster detection. Both resolve via
`sec.form4_transactions`.

## Acceptance gates

Before consuming Form 4 signals:

1. **Upstream freshness** — `sec.filings` has at least one row dated
   within 36 hours; without it, this ingester has nothing to discover.
2. **`sec.form4_normalized_filings` keeping up** — running
   `SELECT COUNT(*) FROM sec.filings WHERE form_type IN ('4', '4/A')` minus
   `SELECT COUNT(*) FROM sec.form4_normalized_filings` should trend
   toward zero. A growing backlog signals the daily run isn't budget-
   sized for incoming volume.
3. **No partial-endpoint failures clustered on one issuer** — soft
   failures per filing are acceptable; a single CIK accounting for
   >50% of failures signals a sec.gov / Archives drift specific to
   that issuer.
4. **Transaction codes within the documented set** — `SELECT DISTINCT
   transaction_code FROM sec.form4_transactions` matches the
   `{P, S, A, F, M, G, J}` set + canonical extensions; a new letter
   signals SEC introduced a new code.
5. **`SEC_USER_AGENT` not the project default** (same gate as parent).

## Follow-ups

- **Form 3 / Form 5 coverage** — both use the same XML shape with
  minor schema variations.
- **Cluster-detection emitter** — `genkei insider-clusters` already
  exists as a CLI; wire it into `meta.signal_events` (B-064 follow-up;
  the `insider_clusters` emitter is the reference implementation).
- **Multi-class consolidation view** — derived materialized view
  unioning GOOG + GOOGL etc. for query convenience.
