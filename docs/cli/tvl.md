# `genkei tvl`

DeFiLlama chain / protocol TVL (default: chains overview).

## Options

```text
Usage: python -m genkei.cli tvl [OPTIONS]                                      
                                                                                
 DeFiLlama chain / protocol TVL (default: chains overview).                     
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --chain                        TEXT                  Chain name, e.g.        │
│                                                      Ethereum, Solana,       │
│                                                      Bitcoin.                │
│ --protocol                     TEXT                  DeFiLlama protocol      │
│                                                      slug, e.g. aave-v3,     │
│                                                      lido.                   │
│ --since                        TEXT                  Start date              │
│                                                      (YYYY-MM-DD).           │
│ --until                        TEXT                  End date (YYYY-MM-DD).  │
│ --limit                        INTEGER RANGE [x>=1]  Max rows. [default: 30] │
│ --max-snapshot-age-hou…        FLOAT RANGE [x>=1]    Warn on stderr when the │
│                                                      freshest returned TVL   │
│                                                      row is older than this  │
│                                                      many hours (default     │
│                                                      36h). The --json row    │
│                                                      list on stdout is never │
│                                                      altered.                │
│                                                      [default: 36.0]         │
│ --json                                               Emit machine-readable   │
│                                                      JSON instead of human   │
│                                                      table.                  │
│ --help                                               Show this message and   │
│                                                      exit.                   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei tvl --chain Ethereum --limit 3
Ethereum TVL (3 rows)
---------------------
  ts                                      tvl_usd
  2026-06-25T19:00:00-05:00        36,683,553,438
  2026-06-24T19:00:00-05:00        37,119,610,310
  2026-06-23T19:00:00-05:00        38,090,609,911
```

**JSON (`--json`)**

```console
$ genkei tvl --chain Ethereum --limit 3 --json
[
  {
    "ts": "2026-06-25T19:00:00-05:00",
    "tvl_usd": 36683553438.0
  },
  {
    "ts": "2026-06-24T19:00:00-05:00",
    "tvl_usd": 37119610310.0
  },
  {
    "ts": "2026-06-23T19:00:00-05:00",
    "tvl_usd": 38090609911.0
  }
]
```

## See also

[`tvl-drawdown`](tvl-drawdown.md) · [`stablecoin-flow`](stablecoin-flow.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
