# `genkei holdings`

SEC 13F institutional holdings (--filer / --filer-cik / --cusip).

## Options

```text
Usage: python -m genkei.cli holdings [OPTIONS]                                 
                                                                                
 SEC 13F institutional holdings (--filer / --filer-cik / --cusip).              
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --filer              TEXT                  Filer by watchlist name (e.g.     │
│                                            'Berkshire Hathaway Inc').        │
│ --filer-cik          TEXT                  Filer by SEC CIK (10-digit;       │
│                                            auto-padded).                     │
│ --cusip              TEXT                  Holdings of a specific            │
│                                            9-character CUSIP across all      │
│                                            watchlist filers.                 │
│ --period             TEXT                  Single quarter-end (YYYY-MM-DD).  │
│                                            Defaults to latest available.     │
│ --since              TEXT                  Earliest period_of_report         │
│                                            (YYYY-MM-DD).                     │
│ --until              TEXT                  Latest period_of_report           │
│                                            (YYYY-MM-DD).                     │
│ --all-periods                              Don't restrict to the latest      │
│                                            period; return rows from every    │
│                                            period.                           │
│ --top                INTEGER RANGE [x>=1]  Max rows (sorted by value_usd     │
│                                            desc).                            │
│                                            [default: 25]                     │
│ --json                                     Emit machine-readable JSON        │
│                                            instead of human table.           │
│ --config             PATH                  Watchlist path.                   │
│                                            [default:                         │
│                                            /Users/michaeltookes/Desktop/Cur… │
│                                            Projects/genkei-capital-fund-res… │
│ --help                                     Show this message and exit.       │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

_Illustrative — the example invocation needs lake data/args not available at capture time. Run it against your lake:_

```console
genkei holdings --filer-cik 1067983 --limit 3
genkei holdings --filer-cik 1067983 --limit 3 --json
```

## See also

[`crowding`](crowding.md) · [`filings`](filings.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
