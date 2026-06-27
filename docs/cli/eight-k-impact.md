# `genkei eight-k-impact`

8-K filing impact event study (B-057) — does an 8-K predict short-run drift?

## Options

```text
Usage: python -m genkei.cli eight-k-impact [OPTIONS]                           
                                                                                
 8-K filing impact event study (B-057) — does an 8-K predict short-run drift?   
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --ticker                             TEXT                                    │
│ --since                              TEXT                                    │
│ --until                              TEXT                                    │
│ --by                                 TEXT     [default:                      │
│                                               ticker,item-code,regime]       │
│ --top                                INTEGER  [default: 10]                  │
│ --json-output    --no-json-output             [default: no-json-output]      │
│ --help                                        Show this message and exit.    │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei eight-k-impact --ticker AAPL
8-K filing impact event study (B-057) — 234 events [horizon=equity:core]
================================================================================

Overall (n=234)
  ALL                    234      0.582%      0.454%      0.032%      0.614%      2.054%

By ticker
  stratum                  n     pre_5d_mean   same_day_mean    post_1d_mean    post_5d_mean   post_30d_mean
  AAPL                   234      0.582%      0.454%      0.032%      0.614%      2.054%

By 8-K item code
  stratum                  n     pre_5d_mean   same_day_mean    post_1d_mean    post_5d_mean   post_30d_mean
  9.01                   150      0.708%      0.851%      0.081%      0.661%      2.699%
  2.02                    93      0.382%      0.936%      0.025%      0.652%      2.820%
  8.01                    43      1.146%      0.360%      0.037%      0.665%      1.177%
  5.02                    38     -0.122%     -0.227%      0.011%      0.440%      5.079%
  5.07                    17      0.346%     -0.476%     -0.063%      0.561%      2.142%
  5.03                    15      0.848%      0.465%      0.380%     -0.453%     -0.547%
... (12 more lines)
```

## See also

[`filings`](filings.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
