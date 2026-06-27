# `genkei etf-flows`

Spot crypto ETF daily activity — sum(volume x close) per asset.

## Options

```text
Usage: python -m genkei.cli etf-flows [OPTIONS]                                
                                                                                
 Spot crypto ETF daily activity - sum(volume x close) per asset across          
 configured ETFs.                                                               
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --asset      -a      TEXT                  Underlying asset: BTC (or         │
│                                            bitcoin) or ETH (or ethereum).    │
│ --since              TEXT                  Earliest flow_date (YYYY-MM-DD).  │
│ --until              TEXT                  Latest flow_date (YYYY-MM-DD).    │
│ --limit              INTEGER RANGE [x>=1]  Max rows. [default: 60]           │
│ --by-ticker                                Per-ETF rows instead of           │
│                                            asset-level aggregate.            │
│ --net-flow                                 Signed daily net flow per         │
│                                            BlackRock ETF from                │
│                                            etf.fund_snapshots                │
│                                            (IBIT/ETHA/ETHB only). Default    │
│                                            mode is dollar-volume from        │
│                                            yahoo.candles.                    │
│ --json                                     Emit machine-readable JSON        │
│                                            instead of human table.           │
│ --list-etfs                                List configured spot crypto ETFs  │
│                                            and exit.                         │
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
genkei etf-flows
genkei etf-flows --json
```

## See also

[`whales`](whales.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
