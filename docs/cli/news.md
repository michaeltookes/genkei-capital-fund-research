# `genkei news`

GDELT GKG article clusters — filter by watchlist asset / theme / topic / tone.

## Options

```text
Usage: python -m genkei.cli news [OPTIONS]                                     
                                                                                
 GDELT GKG article clusters — filter by watchlist asset / theme / topic / tone. 
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --ticker    -t      TEXT                  Filter to a watchlist equity       │
│                                           (matched_assets contains TICKER).  │
│ --asset     -a      TEXT                  Filter to a watchlist crypto       │
│                                           (matched_assets contains SYMBOL).  │
│ --theme             TEXT                  Filter to a GDELT theme (themes    │
│                                           array contains THEME).             │
│ --topic             TEXT                  Free-text substring across         │
│                                           document URL + themes (ILIKE).     │
│ --since             TEXT                  Earliest publication date          │
│                                           (YYYY-MM-DD).                      │
│ --until             TEXT                  Latest publication date            │
│                                           (YYYY-MM-DD).                      │
│ --tone-min          FLOAT                 Minimum article tone (-100..100;   │
│                                           GDELT V1.5 avg).                   │
│ --tone-max          FLOAT                 Maximum article tone (-100..100;   │
│                                           GDELT V1.5 avg).                   │
│ --source            TEXT                  Exact-match source_common_name     │
│                                           (e.g. 'nytimes.com').              │
│ --limit             INTEGER RANGE [x>=1]  Max clusters to return.            │
│                                           [default: 30]                      │
│ --json                                    Emit machine-readable JSON instead │
│                                           of human table.                    │
│ --config            PATH                  Watchlist path.                    │
│                                           [default:                          │
│                                           /Users/michaeltookes/Desktop/Curr… │
│                                           Projects/genkei-capital-fund-rese… │
│ --help                                    Show this message and exit.        │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei news --limit 3
GDELT news clusters | pool_size=915 | 3 clusters
------------------------------------------------
  2026-06-23  iheart.com                   articles= 61  tone=  -5.97  assets=BTC
    - https://powertalk1360.iheart.com/content/2026-06-23-guthrie-makes-plea-after-note-claimed-mother-died/
    - https://knst.iheart.com/content/2026-06-23-guthrie-makes-plea-after-note-claimed-mother-died/
    - https://ktok.iheart.com/content/2026-06-23-guthrie-makes-plea-after-note-claimed-mother-died/
  2026-06-25  yahoo.com                    articles= 20  tone=  -0.86  assets=BTC,ETH,MSFT,TSM
    - https://finance.yahoo.com/markets/stocks/articles/microstrategy-stock-drops-below-100-135015827.html
    - https://finance.yahoo.com/markets/crypto/articles/perpetual-futures-where-trade-them-140023480.html
    - https://finance.yahoo.com/markets/crypto/articles/kraken-buy-15-stake-defi-213400221.html
  2026-06-24  yahoo.com                    articles= 12  tone=  -1.61  assets=BTC,ETH,SOL
    - https://finance.yahoo.com/markets/crypto/articles/robert-kiyosaki-says-bitcoin-ethereum-100109160.html
    - https://finance.yahoo.com/markets/crypto/articles/prediction-solana-top-crypto-decentralized-100400957.html
    - https://finance.yahoo.com/markets/crypto/articles/bitcoin-news-digital-dollar-blocked-093609354.html

  Horizon: cross-sleeve | sleeve: research/news (consume alongside prices, filings, on-chain)
```

**JSON (`--json`)**

```console
$ genkei news --limit 3 --json
{
  "horizon": "cross-sleeve | sleeve: research/news",
  "summary": {
    "ticker": null,
    "asset": null,
    "theme": null,
    "topic": null,
    "since": null,
    "until": null,
    "tone_min": null,
    "tone_max": null,
    "source": null,
    "pool_size": 915
  },
... (50 more lines)
```

## See also

[`news-sentiment`](news-sentiment.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
