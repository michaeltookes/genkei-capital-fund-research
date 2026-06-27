# `genkei filings`

SEC EDGAR filings (default) or XBRL facts (--concept).

## Options

```text
Usage: python -m genkei.cli filings [OPTIONS]                                  
                                                                                
 SEC EDGAR filings (default) or XBRL facts (--concept).                         
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ *  --ticker   -t      TEXT                  Equity ticker, e.g. AAPL.        │
│                                             [required]                       │
│    --form             TEXT                  Filter by SEC form type, e.g.    │
│                                             10-K, 8-K, 4.                    │
│    --concept          TEXT                  XBRL concept, e.g. Revenues or   │
│                                             us-gaap:Revenues. Switches       │
│                                             output to sec.facts.             │
│    --unit             TEXT                  Filter facts by unit, e.g. USD,  │
│                                             shares, USD/shares.              │
│    --since            TEXT                  Start date (YYYY-MM-DD).         │
│                                             filed_at for filings, period_end │
│                                             for facts.                       │
│    --until            TEXT                  End date (YYYY-MM-DD).           │
│    --limit            INTEGER RANGE [x>=1]  Max rows. [default: 30]          │
│    --json                                   Emit machine-readable JSON       │
│                                             instead of human table.          │
│    --config           PATH                  Watchlist path.                  │
│                                             [default:                        │
│                                             /Users/michaeltookes/Desktop/Cu… │
│                                             Projects/genkei-capital-fund-re… │
│    --help                                   Show this message and exit.      │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei filings --ticker AAPL --limit 3
AAPL filings (3 rows, horizon=equity:core:primary)
--------------------------------------------------
  filed        form       report        accession
  2026-06-17   4          2026-06-15    0001140361-26-025622
  2026-06-17   4          2026-06-15    0001140361-26-025620
  2026-05-29   4          2026-05-27    0001140361-26-023363
```

**JSON (`--json`)**

```console
$ genkei filings --ticker AAPL --limit 3 --json
[
  {
    "accession_number": "0001140361-26-025622",
    "form_type": "4",
    "filed_at": "2026-06-17",
    "report_date": "2026-06-15",
    "primary_document": "xslF345X06/form4.xml",
    "primary_doc_description": "FORM 4",
    "items": null,
    "is_xbrl": false,
    "horizon_tag": "equity:core:primary"
  },
  {
    "accession_number": "0001140361-26-025620",
... (21 more lines)
```

## See also

[`insiders`](insiders.md) · [`holdings`](holdings.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
