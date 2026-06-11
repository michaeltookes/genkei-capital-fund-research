# Treasury Fiscal Data ingester (B-030)

The U.S. Treasury publishes debt issuance, the Treasury operating
cash balance, monthly interest expense, and weighted-average yields
per security class through the open
[Fiscal Data API](https://fiscaldata.treasury.gov/api-documentation/).
This ingester is the **public-debt + cost-of-debt + Treasury cash**
companion to FRED (rates/credit/vol/FX, B-028) and BEA (real-economy
growth, B-029) already in the lake.

## Coverage v1

The watchlist seeds **9 series across 4 endpoints** spanning three
load-bearing fiscal dimensions:

| Dimension | series_id | Endpoint | Cadence |
|---|---|---|---|
| **Debt level** | `TOTAL_PUBLIC_DEBT`, `DEBT_HELD_PUBLIC`, `INTRAGOV_HOLDINGS` | `/v2/accounting/od/debt_to_penny` | Daily |
| **Treasury cash** | `TGA_CLOSING_BAL` | `/v1/accounting/dts/operating_cash_balance` | Daily |
| **Cost of debt — actual** | `TOTAL_INTEREST_EXPENSE_MTD` | `/v2/accounting/od/interest_expense` | Monthly |
| **Cost of debt — avg rate** | `AVG_RATE_TOTAL_INTEREST_BEARING`, `AVG_RATE_MARKETABLE_BILLS`, `AVG_RATE_MARKETABLE_NOTES`, `AVG_RATE_MARKETABLE_BONDS` | `/v2/accounting/od/avg_interest_rates` | Monthly |

`series_id` is a friendly TEXT key chosen in the watchlist (e.g.
`TOTAL_PUBLIC_DEBT`). Each entry binds a specific endpoint +
`value_field` + optional `row_filter` projection. The full curated list
lives in `src/genkei/data/watchlists.yml` under the `treasury:` section.

### Why these series

- **`TOTAL_PUBLIC_DEBT`** / **`DEBT_HELD_PUBLIC`** / **`INTRAGOV_HOLDINGS`** —
  the headline national-debt level, daily. `debt_held_public` is the
  supply markets actually price; `intragov_hold` is SS trust funds +
  similar non-market holdings.
- **`TGA_CLOSING_BAL`** — daily Treasury General Account balance. Load-
  bearing liquidity signal: a draining TGA injects cash into the system
  (bullish liquidity); a refill drains it (bearish). v1 covers ~April
  2022 onward due to the DTS reporting-format change; pre-2022 backfill
  deferred to v2.
- **`TOTAL_INTEREST_EXPENSE_MTD`** — actual monthly debt-service cost.
  Rises mechanically as the average rate × debt level grows.
- **`AVG_RATE_TOTAL_INTEREST_BEARING`** — blended cost-of-debt across the
  whole federal-debt stack. The summary number.
- **`AVG_RATE_MARKETABLE_BILLS`** / **`NOTES`** / **`BONDS`** — per-class
  weighted-average yields. Bills track the policy rate; notes drive
  near-term refi pressure; bonds carry the term premium.

## Endpoints NOT covered (deferred)

- **`/v1/accounting/od/auctions_query`** — per-auction primary-market
  results (security_term, high_yield, bid_to_cover, total_tendered).
  Defer to v2: it's **event-shaped** (one row per auction with rich
  metadata), not a time series. A separate `treasury.auctions` event
  table is the right schema, not the current `(series_id, ts, value)`
  shape.
- Detailed **DTS Tables II–V** (deposits/withdrawals, debt activity,
  short-term cash investments) — useful for liquidity-flow attribution
  but not required by the current macro-regime / cost-of-debt sleeves.
  Reopen as a separate backlog item when needed.

## Endpoint contract

- **Base URL**: `https://api.fiscaldata.treasury.gov/services/api/fiscal_service`
- **Auth**: **none — Treasury Fiscal Data is fully open**. No API key
  registration, no auth header, no key-redaction concerns.
- **Rate limit**: undocumented. We use **2 req/s** as the "be polite"
  default; the full v1 watchlist (4 unique endpoint fetches × a handful
  of pages each) finishes in under a minute.
- **Fetch shape**: one URL family per unique endpoint, **not** per
  series. Multiple series can share an endpoint (the three `debt_to_penny`
  columns and the four `avg_interest_rates` rows); the collector fetches
  the endpoint once and the normalizer filters down to each watched
  `(value_field, row_filter)` projection at parse time. Fewer API calls,
  simpler error recovery, lower partial-state risk.
- **Pagination**: standard `page[number]` + `page[size]` (max 10,000).
  The collector loops every page using the response's `meta.total-pages`
  as the loop bound. ~30 years × daily debt_to_penny = ~3 pages at
  `page[size]=10000`. Requests sort by `record_date` plus endpoint-specific
  row keys for multi-row endpoints (`account_type`, `expense_catg_desc`,
  or `security_type_desc` + `security_desc`) so equal-date rows cannot
  move across page boundaries between page requests.
- **Response envelope**:
  ```json
  {
    "data": [
      {
        "record_date": "2024-06-10",
        "tot_pub_debt_out_amt": "34,176,659,847,936.05",
        "debt_held_public_amt": "27,654,231,894,612.11",
        "intragov_hold_amt": "6,522,427,953,323.94"
      }
    ],
    "meta": {
      "count": 1,
      "labels": {...},
      "dataTypes": {...},
      "total-count": 11234,
      "total-pages": 2
    },
    "links": {
      "self": "...",
      "first": "...",
      "next": "?page[number]=2&...",
      "last": "...",
      "prev": null
    }
  }
  ```
- **Dates** are published as `YYYY-MM-DD` strings under the field name
  configured per series (`record_date` for every v1 endpoint).
- **Numeric values** are published as strings, often with thousands
  separators (`"34,176,659,847,936.05"`). Missing values arrive as the
  literal string `"null"` (not JSON `null`).

## Schema

| Table | Purpose | PK |
|---|---|---|
| `treasury.series` | Entity dim — name, endpoint, value_field, row_filter (JSONB), units, frequency, source provenance | `series_id` (TEXT, friendly key from the watchlist) |
| `treasury.observations` | Time-series fact, hypertable on `ts`, 90-day chunks, compression > 30d, `segmentby=series_id` | `(series_id, ts)` |

**`row_filter` is JSONB on the series dim** so consumers can see at a
glance which row of a multi-row endpoint a series projects from (e.g.
`TGA_CLOSING_BAL` selects the row where
`account_type = "Treasury General Account (TGA) Closing Balance"`).

**`frequency` is NOT in the PK** (unlike BEA's `(series_id, ts, frequency)`).
Each Treasury series is locked to one cadence by construction —
`debt_to_penny` is daily, `interest_expense` is monthly. If we ever
want the same underlying line at two cadences, that's two watchlist
entries with distinct series_ids.

## v1 limitations (worth knowing before relying on the data)

1. **Latest-only, NOT vintage-aware**. Treasury revises in place at the
   source. The migration's PK omits a vintage column to match. v2 would
   add `fetched_at_date` to the PK if we ever need an as-of revision
   trail.

2. **No backfill flag** like FRED's `--backfill --since`. The Fiscal
   Data API returns full history by default (paginated); daily and
   "backfill" runs are the same code path. The latest-only schema doesn't
   grow unbounded — each daily run upserts every observation.

3. **`"null"` is the missing-value sentinel** (literal string, not JSON
   `null`). The parser maps it to `value IS NULL` in
   `treasury.observations`. Empty strings, `"N/A"`, `"-"`, and case
   variants of `"null"` are all treated the same way.

4. **Thousands-separated values** (e.g. `"34,176,659,847,936.05"`) are
   stripped before float conversion. Native numeric responses pass
   through.

5. **TGA pre-2022 coverage**. The DTS reporting format changed in April
   2022; the `account_type` value the v1 `TGA_CLOSING_BAL` filter selects
   only exists from then forward. Pre-2022 TGA history would require a
   second series_id with a different `row_filter`. Filed as natural
   follow-up.

6. **Polite-default rate limit** of 2 req/s. Treasury publishes no
   documented limit. If a future use case (bigger v2 watchlist) needs
   tighter pacing the `DEFAULT_RATE_LIMIT` in `genkei.ingest.treasury`
   is the single tunable.

7. **No 200-OK-with-error envelope handling** like BEA. The Fiscal Data
   API consistently returns proper HTTP status codes on failure (400 on
   bad params, 5xx on outages); the collector's `httpx.HTTPStatusError`
   path catches both. If a contract change introduces 200-with-error
   responses, we'll learn from the missing-series raise in the
   normalizer.

## How it runs

- **Cron**: daily at **12:00 UTC** (`.github/workflows/treasury-daily.yml`)
  on the self-hosted Beelink runner. Slots 30 minutes after BEA-daily so
  the free-API macro trio (FRED → BEA → Treasury) stays staggered on the
  runner's morning slot.
- **Manual replay**:
  ```bash
  python -m genkei.ingest.treasury     # collect
  python -m genkei.normalize.treasury  # normalize (auto-finds latest collect)
  ```
- **Watchlist health**: registered in `PRIMARY_TABLES` +
  `RECURRING_ENDPOINTS`. `genkei watchlist health` surfaces
  OK / STALE / FAIL / MISSING / EMPTY for the `treasury` source like
  every other ingester.

## Querying the data

Today the path is `genkei query` over `treasury.observations`:

```bash
genkei query --sql "
  SELECT o.series_id, s.name, o.ts, o.value
  FROM treasury.observations o
  JOIN treasury.series s USING (series_id)
  WHERE o.series_id IN ('TGA_CLOSING_BAL', 'TOTAL_PUBLIC_DEBT')
    AND o.ts >= now() - INTERVAL '90 days'
  ORDER BY o.series_id, o.ts DESC
"
```

A typed CLI surface (`genkei fiscal --series TGA_CLOSING_BAL`, or a
standalone `genkei treasury`) is a natural follow-up but not in v1
scope. The lake data is what matters most; the agent + the SQL
escape hatch close the gap in the meantime.

## Natural follow-ups

- **`auctions_query` v2** — separate `treasury.auctions` event table
  capturing per-auction security_term, high_yield, bid_to_cover, and
  total_tendered. The current schema doesn't fit; auctions are
  event-shaped, not time-series.
- **Pre-2022 TGA coverage** — second `TGA_CLOSING_BAL_LEGACY` series
  with a different `row_filter` against the pre-format-change
  `account_type` strings.
- **Treasury cost-of-debt experiment** — pair `TOTAL_INTEREST_EXPENSE_MTD`
  with `TOTAL_PUBLIC_DEBT` to compute implied avg rate and compare against
  the explicit `AVG_RATE_TOTAL_INTEREST_BEARING` — sanity check on
  Treasury's own weighted-average calculation.
- **CLI surface** — `genkei fiscal --series ...` or `genkei treasury` for
  type-safe querying. Today's path is `genkei query` + raw SQL.
- **B-066 regime classifier** — surface `TGA_CLOSING_BAL` Δ and avg-rate
  trajectory as additional macro-regime inputs once enough history
  accumulates.
