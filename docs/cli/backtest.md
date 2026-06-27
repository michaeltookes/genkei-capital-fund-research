# `genkei backtest`

Stack-outcome backtest (B-101) — do historical stacks predict forward returns?

## Options

```text
Usage: python -m genkei.cli backtest [OPTIONS]                                 
                                                                                
 Stack-outcome backtest (B-101) — do historical stacks predict forward returns? 
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --by                        TEXT  Stratify results by rule / direction /     │
│                                   asset.                                     │
│                                   [default: rule]                            │
│ --asset             -a      TEXT  Limit to one asset (equity ticker or       │
│                                   crypto id).                                │
│ --rule                      TEXT  Limit to one correlation rule.             │
│ --direction                 TEXT  Filter to bullish / bearish / neutral.     │
│ --since                     TEXT  Earliest stack window_end date             │
│                                   (YYYY-MM-DD).                              │
│ --until                     TEXT  Latest stack window_end date (YYYY-MM-DD). │
│ --equity-benchmark          TEXT  Benchmark ticker for equity stacks'        │
│                                   abnormal-return column (yahoo.candles).    │
│                                   [default: SPY]                             │
│ --crypto-benchmark          TEXT  Benchmark ticker for crypto stacks'        │
│                                   abnormal-return column (coinbase.candles). │
│                                   [default: BTC]                             │
│ --no-benchmark                    Skip the benchmark-adjusted                │
│                                   abnormal-return column entirely.           │
│ --json                            Emit machine-readable JSON.                │
│ --rules-path                PATH  Override the signal-rules YAML location.   │
│                                   [default:                                  │
│                                   /Users/michaeltookes/Desktop/Current       │
│                                   Projects/genkei-capital-fund-research/src… │
│ --config                    PATH  Watchlist path.                            │
│                                   [default:                                  │
│                                   /Users/michaeltookes/Desktop/Current       │
│                                   Projects/genkei-capital-fund-research/src… │
│ --help                            Show this message and exit.                │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

_Illustrative — run against your lake to see live output._

```console
genkei backtest --help   # see flags; this command runs an experiment/heavy query
```

## See also

[`signals`](signals.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
