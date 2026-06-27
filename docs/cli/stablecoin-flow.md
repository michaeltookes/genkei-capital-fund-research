# `genkei stablecoin-flow`

Cross-chain stablecoin supply trajectory + rotation (--all-chains).

## Options

```text
Usage: python -m genkei.cli stablecoin-flow [OPTIONS]                          
                                                                                
 Cross-chain stablecoin supply trajectory + rotation signal (per-chain or       
 --all-chains).                                                                 
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --chain          -c      TEXT                  Chain name (Ethereum, Solana, │
│                                                …) or alias (eth, sol, tron). │
│ --all-chains                                   Comparative snapshot across   │
│                                                all chains with material      │
│                                                supply.                       │
│ --by-stablecoin                                Per-asset (USDT / USDC / DAI  │
│                                                / …) split for the latest day │
│                                                of --chain.                   │
│ --since                  TEXT                  Earliest day (YYYY-MM-DD) for │
│                                                trajectory mode.              │
│ --until                  TEXT                  Latest day (YYYY-MM-DD) for   │
│                                                trajectory mode.              │
│ --limit                  INTEGER RANGE [x>=1]  Max rows. [default: 60]       │
│ --min-supply-b           FLOAT RANGE [x>=0.0]  Minimum chain supply (USD     │
│                                                billions) for --all-chains;   │
│                                                filter the long tail.         │
│                                                [default: 0.5]                │
│ --json                                         Emit machine-readable JSON    │
│                                                instead of human table.       │
│ --list-chains                                  List every chain with         │
│                                                stablecoin data and exit.     │
│ --help                                         Show this message and exit.   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei stablecoin-flow --all-chains
Stablecoin supply by chain | all-chains snapshot | 18 chains
------------------------------------------------------------
  chain            day            supply_$B    Δ_7d_$B   Δ_30d_$B
  Ethereum         2026-06-25        156.26      -1.18      -6.80
  Tron             2026-06-25         89.10      -0.21      -1.08
  BSC              2026-06-25         17.80      +0.04      -0.16
  Solana           2026-06-25         15.48      -0.19      +0.21
  Hyperliquid L1   2026-06-25          5.98      -0.43      -0.80
  Base             2026-06-25          4.80      +0.01      +0.17
  Arbitrum         2026-06-25          3.95      -0.02      -0.31
  Polygon          2026-06-25          3.33      -0.08      -0.24
  Aptos            2026-06-25          1.87      -0.06      +0.12
  X Layer          2026-06-25          1.82      +0.07      +0.35
  Avalanche        2026-06-25          1.52      -0.02      -0.24
  Plasma           2026-06-25          1.10      -0.01      +0.16
  XRPL             2026-06-25          0.85      +0.07      +0.10
  Stellar          2026-06-25          0.80      +0.01      +0.49
  TON              2026-06-25          0.76      -0.01      -0.04
... (5 more lines)
```

**JSON (`--json`)**

```console
$ genkei stablecoin-flow --all-chains --json
[
  {
    "chain": "Ethereum",
    "day": "2026-06-25",
    "supply_usd_b": 156.25699235263488,
    "delta_7d_usd_b": -1.1849323002139263,
    "delta_30d_usd_b": -6.798024122296762
  },
  {
    "chain": "Tron",
    "day": "2026-06-25",
    "supply_usd_b": 89.09702505344573,
    "delta_7d_usd_b": -0.2075563814092236,
    "delta_30d_usd_b": -1.0766423496599649
... (114 more lines)
```

## See also

[`tvl`](tvl.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
