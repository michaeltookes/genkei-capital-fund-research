# `genkei prices`

Crypto + equity prices from the lake (CoinGecko / Coinbase / Yahoo).

## Options

```text
Usage: python -m genkei.cli prices [OPTIONS]                                   
                                                                                
 Asset prices (crypto today; equities later).                                   
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ *  --ticker              -t      TEXT                  Asset ticker, e.g.    │
│                                                        BTC.                  │
│                                                        [required]            │
│    --source                      TEXT                  Price source.         │
│                                                        `coingecko` (crypto,  │
│                                                        365d window),         │
│                                                        `coinbase` (crypto,   │
│                                                        long history per      │
│                                                        B-035), or `yahoo`    │
│                                                        (equities, long       │
│                                                        history per B-092).   │
│                                                        Equity tickers        │
│                                                        default to `yahoo`;   │
│                                                        crypto tickers        │
│                                                        default to            │
│                                                        `coingecko`.          │
│    --since                       TEXT                  Start date            │
│                                                        (YYYY-MM-DD).         │
│    --until                       TEXT                  End date              │
│                                                        (YYYY-MM-DD).         │
│    --limit                       INTEGER RANGE [x>=1]  Max rows.             │
│                                                        [default: 30]         │
│    --max-snapshot-age-…          FLOAT RANGE [x>=1]    Warn on stderr when   │
│                                                        the freshest returned │
│                                                        row is older than     │
│                                                        this many hours       │
│                                                        (default 36h, a       │
│                                                        daily-cadence         │
│                                                        cutoff). The --json   │
│                                                        row list on stdout is │
│                                                        never altered.        │
│                                                        [default: 36.0]       │
│    --json                                              Emit machine-readable │
│                                                        JSON instead of human │
│                                                        table.                │
│    --config                      PATH                  Watchlist path.       │
│                                                        [default:             │
│                                                        /Users/michaeltookes… │
│                                                        Projects/genkei-capi… │
│    --help                                              Show this message and │
│                                                        exit.                 │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei prices --ticker BTC --limit 3
BTC prices (source: coingecko, 3 rows)
--------------------------------------
  timestamp                     price (USD)        market cap            volume
  2026-06-26T08:35:12-05:00       58,833.68  1,181,378,761,069    50,129,322,482
  2026-06-25T19:00:00-05:00       59,712.62  1,197,252,258,228    40,411,370,315
  2026-06-25T08:49:50-05:00       59,381.72  1,193,960,876,477    45,068,470,573
```

**JSON (`--json`)**

```console
$ genkei prices --ticker BTC --limit 3 --json
[
  {
    "ts": "2026-06-26T08:35:12-05:00",
    "price_usd": 58833.6751938256,
    "market_cap_usd": 1181378761068.68,
    "volume_usd": 50129322481.9302
  },
  {
    "ts": "2026-06-25T19:00:00-05:00",
    "price_usd": 59712.6180615821,
    "market_cap_usd": 1197252258228.06,
    "volume_usd": 40411370315.0141
  },
  {
... (6 more lines)
```

## See also

[`tvl`](tvl.md) · [`relative-strength`](relative-strength.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
