# `genkei crowding`

13F crowding monitor — top crowded watchlist names per quarter + deltas.

## Options

```text
Usage: python -m genkei.cli crowding [OPTIONS]                                 
                                                                                
 13F crowding monitor — top crowded watchlist names per quarter + deltas.       
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --ticker       -t      TEXT                  Equity ticker (resolves to      │
│                                              CUSIP via the watchlist).       │
│ --cusip                TEXT                  9-char SEC CUSIP.               │
│ --period               TEXT                  Single quarter-end              │
│                                              (YYYY-MM-DD). Default: latest   │
│                                              available.                      │
│ --since                TEXT                  Earliest period_of_report       │
│                                              (YYYY-MM-DD).                   │
│ --until                TEXT                  Latest period_of_report         │
│                                              (YYYY-MM-DD).                   │
│ --all-periods                                Return rows from every period   │
│                                              in the lake (no period filter). │
│ --min-holders          INTEGER RANGE [x>=1]  Render only rows with ≥N        │
│                                              holders at the current period.  │
│                                              [default: 2]                    │
│ --by-delta                                   Sort by net_change desc         │
│                                              (biggest adds first) instead of │
│                                              holder_count.                   │
│ --top                  INTEGER RANGE [x>=1]  Max rows. [default: 25]         │
│ --json                                       Emit machine-readable JSON.     │
│ --config               PATH                  Watchlist path.                 │
│                                              [default:                       │
│                                              /Users/michaeltookes/Desktop/C… │
│                                              Projects/genkei-capital-fund-r… │
│ --help                                       Show this message and exit.     │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei crowding
13F crowding (25 row(s), ≥2 holders, latest period 2026-03-31, by holder_count desc)
------------------------------------------------------------------------------------
  period       tkr    horizon              cusip          # Δvs prior                        $value  top holders
  2026-03-31   GOOGL  equity:core:primary  02079K305      8 +2 (6→8)            $20,775,607,448,000  BERKSHIRE HATHAWAY INC, TIGER GLOBAL MANAGEMENT LLC, TWO SIGMA INVESTMENTS, +5 more
  2026-03-31   META   equity:core:primary  30303M102      8 +2 (6→8)             $4,711,866,021,000  TIGER GLOBAL MANAGEMENT LLC, Pershing Square Capital Management, ValueAct Holdings, +5 more
  2026-03-31   TSM    equity:core:primary  874039100      7 +1 (6→7)             $3,416,902,955,000  TIGER GLOBAL MANAGEMENT LLC, LONE PINE CAPITAL LLC, Bridgewater Associates, +4 more
  2026-03-31   -      equity:unknown       L8681T102      7 +1 (6→7)             $1,126,618,909,000  TIGER GLOBAL MANAGEMENT LLC, ValueAct Holdings, RENAISSANCE TECHNOLOGIES LLC, +4 more
  2026-03-31   AMZN   equity:core:primary  023135106      7 -3 (10→7)            $7,988,350,484,000  Pershing Square Capital Management, TIGER GLOBAL MANAGEMENT LLC, TWO SIGMA INVESTMENTS, +4 more
  2026-03-31   AVGO   equity:core:primary  11135F101      6 +2 (4→6)             $2,753,722,250,000  TIGER GLOBAL MANAGEMENT LLC, TWO SIGMA INVESTMENTS, Bridgewater Associates, +3 more
  2026-03-31   NVDA   equity:core:primary  67066G104      6 +0 (6→6)             $5,526,971,899,000  TIGER GLOBAL MANAGEMENT LLC, TWO SIGMA INVESTMENTS, Bridgewater Associates, +3 more
  2026-03-31   MSFT   equity:core:primary  594918104      6 -1 (7→6)             $3,937,052,675,000  Pershing Square Capital Management, TIGER GLOBAL MANAGEMENT LLC, TWO SIGMA INVESTMENTS, +3 more
  2026-03-31   GOOG   equity:core:primary  02079K107      5 +2 (3→5)             $1,493,209,425,000  BERKSHIRE HATHAWAY INC, ARK Investment Management LLC, Bridgewater Associates, +2 more
  2026-03-31   -      equity:unknown       512807306      5 +1 (4→5)             $1,506,794,687,000  TIGER GLOBAL MANAGEMENT LLC, Bridgewater Associates, TWO SIGMA INVESTMENTS, +2 more
  2026-03-31   -      equity:unknown       58733R102      5 +1 (4→5)               $558,129,384,000  TIGER GLOBAL MANAGEMENT LLC, RENAISSANCE TECHNOLOGIES LLC, TWO SIGMA INVESTMENTS, +2 more
  2026-03-31   -      equity:unknown       880770102      5 +1 (4→5)             $1,322,589,696,000  LONE PINE CAPITAL LLC, ARK Investment Management LLC, TWO SIGMA INVESTMENTS, +2 more
  2026-03-31   UBER   equity:core:primary  90353T100      5 +1 (4→5)             $2,314,045,213,000  Pershing Square Capital Management, RENAISSANCE TECHNOLOGIES LLC, TWO SIGMA INVESTMENTS, +2 more
  2026-03-31   -      equity:unknown       N07059210      5 +1 (4→5)             $1,503,931,604,000  LONE PINE CAPITAL LLC, TWO SIGMA INVESTMENTS, Bridgewater Associates, +2 more
  2026-03-31   -      equity:unknown       64110L106      5 +0 (5→5)               $294,437,039,000  TIGER GLOBAL MANAGEMENT LLC, TWO SIGMA INVESTMENTS, ARK Investment Management LLC, +2 more
... (10 more lines)
```

**JSON (`--json`)**

```console
$ genkei crowding --json
[
  {
    "period_of_report": "2026-03-31",
    "cusip": "02079K305",
    "issuer_name": "ALPHABET INC",
    "ticker": "GOOGL",
    "horizon_tag": "equity:core:primary",
    "holder_count": 8,
    "holder_ciks": [
      "0001067983",
      "0001167483",
      "0001179392",
      "0001350694",
      "0001037389",
... (776 more lines)
```

## See also

[`holdings`](holdings.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
