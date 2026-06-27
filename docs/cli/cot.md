# `genkei cot`

CFTC Commitments of Traders — weekly position breakdowns per market / trader category.

## Options

```text
Usage: python -m genkei.cli cot [OPTIONS]                                      
                                                                                
 CFTC Commitments of Traders — weekly position breakdowns per market/trader     
 category.                                                                      
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --market           -m      TEXT                  Market symbol (BTC, ETH,    │
│                                                  ES, GC, CL) or CFTC market  │
│                                                  code (133741).              │
│ --trader-category  -c      TEXT                  Filter to one trader        │
│                                                  category (e.g.              │
│                                                  leveraged_funds,            │
│                                                  asset_manager). Aliases     │
│                                                  accepted; see module        │
│                                                  docstring.                  │
│ --since                    TEXT                  Earliest report_date        │
│                                                  (YYYY-MM-DD).               │
│ --until                    TEXT                  Latest report_date          │
│                                                  (YYYY-MM-DD).               │
│ --limit                    INTEGER RANGE [x>=1]  Max rows. [default: 50]     │
│ --json                                           Emit machine-readable JSON  │
│                                                  instead of human table.     │
│ --list-markets                                   List configured COT markets │
│                                                  and exit.                   │
│ --config                   PATH                  Watchlist path.             │
│                                                  [default:                   │
│                                                  /Users/michaeltookes/Deskt… │
│                                                  Projects/genkei-capital-fu… │
│ --help                                           Show this message and exit. │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

_Illustrative — the example invocation needs lake data/args not available at capture time. Run it against your lake:_

```console
genkei cot
genkei cot --json
```

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
