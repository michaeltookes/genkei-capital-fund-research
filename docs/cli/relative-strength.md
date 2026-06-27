# `genkei relative-strength`

Crypto peer relative-strength (asset return - peer return per window).

## Options

```text
Usage: python -m genkei.cli relative-strength [OPTIONS]                        
                                                                                
 Crypto peer relative-strength (asset return - peer return per window).         
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --ticker  -t      TEXT                  Crypto watchlist ticker (BTC, SUI,   │
│                                         …). Filters asset side.              │
│ --peer    -p      TEXT                  Peer crypto watchlist ticker         │
│                                         (default BTC unless --ticker is also │
│                                         set, in which case all windows are   │
│                                         shown for the pair).                 │
│ --window  -w      INTEGER RANGE [x>=1]  Trailing window in days. Default 30d │
│                                         unless --ticker and --peer are both  │
│                                         set, in which case all 5 windows     │
│                                         show.                                │
│ --limit           INTEGER RANGE [x>=1]  Max rows to return. [default: 50]    │
│ --json                                  Emit machine-readable JSON instead   │
│                                         of human table.                      │
│ --config          PATH                  Watchlist path.                      │
│                                         [default:                            │
│                                         /Users/michaeltookes/Desktop/Current │
│                                         Projects/genkei-capital-fund-resear… │
│ --help                                  Show this message and exit.          │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei relative-strength --ticker BTC
No relative-strength rows match. Either the filters excluded everything or analytics.crypto_relative_strength is empty (check `genkei watchlist health`).
```

**JSON (`--json`)**

```console
$ genkei relative-strength --ticker BTC --json
[]
```

## See also

[`prices`](prices.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
