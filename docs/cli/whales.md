# `genkei whales`

ETH whale-address daily flow aggregate (--category or --address).

## Options

```text
Usage: python -m genkei.cli whales [OPTIONS]                                   
                                                                                
 ETH whale-address daily flow aggregate (--category                             
 exchange|custodian|foundation|whale or --address).                             
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --category        -c      TEXT                  Filter to one category:      │
│                                                 exchange / custodian /       │
│                                                 foundation / whale. Aliases: │
│                                                 cex / staking / treasury.    │
│ --address         -a      TEXT                  Per-address rows for one     │
│                                                 wallet (lowercased 0x...     │
│                                                 hex). Mutually exclusive     │
│                                                 with --category.             │
│ --since                   TEXT                  Earliest day (YYYY-MM-DD).   │
│ --until                   TEXT                  Latest day (YYYY-MM-DD).     │
│ --limit                   INTEGER RANGE [x>=1]  Max rows. [default: 60]      │
│ --json                                          Emit machine-readable JSON   │
│                                                 instead of human table.      │
│ --list-addresses                                List configured              │
│                                                 eth_whale_addresses grouped  │
│                                                 by category and exit.        │
│ --config                  PATH                  Watchlist path.              │
│                                                 [default:                    │
│                                                 /Users/michaeltookes/Deskto… │
│                                                 Projects/genkei-capital-fun… │
│ --help                                          Show this message and exit.  │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei whales
ETH whale-flow aggregate | all categories | 3 rows
--------------------------------------------------
  day          category     addrs      total_eth    net_flow_eth   net_flow_usd_mm       tx
  2026-06-08   custodian        3     86,464,692         +19,440             +33.0      604
  2026-06-08   exchange        15        858,275        +141,758            +240.4   28,332
  2026-06-08   foundation       2          9,848              +0              +0.0        0

  net_flow_eth = sum(incoming) - sum(outgoing) over the 24h window per address, summed per category.
  Sign convention: exchange inflow = SELL pressure (users sending TO CEX); custodian inflow = staking commitment; foundation outflow = treasury monetization.
  Horizon: mid-to-long-term | sleeve: crypto-core (ETH) + crypto-tactical
```

**JSON (`--json`)**

```console
$ genkei whales --json
[
  {
    "category": "custodian",
    "day": "2026-06-08",
    "address_count": 3,
    "total_balance_eth": "86464691.560660374964786600",
    "total_balance_usd": "146622366654.12",
    "net_flow_eth": "19439.671638657583997620",
    "net_flow_usd": "32964793.05",
    "tx_count": 604
  },
  {
    "category": "exchange",
    "day": "2026-06-08",
... (18 more lines)
```

## See also

[`etf-flows`](etf-flows.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
