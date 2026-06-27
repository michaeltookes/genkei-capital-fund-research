# `genkei news-sentiment`

News sentiment vs forward returns — Pearson/Spearman + quartiles (B-056).

## Options

```text
Usage: python -m genkei.cli news-sentiment [OPTIONS]                           
                                                                                
 News sentiment vs forward returns — Pearson/Spearman + per-quartile breakdown  
 (B-056).                                                                       
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --ticker                -t      TEXT                  Watchlist equity       │
│                                                       ticker (mutually       │
│                                                       exclusive with         │
│                                                       --asset).              │
│ --asset                 -a      TEXT                  Watchlist crypto       │
│                                                       symbol (mutually       │
│                                                       exclusive with         │
│                                                       --ticker).             │
│ --since                         TEXT                  Earliest publication / │
│                                                       trading date           │
│                                                       (YYYY-MM-DD).          │
│ --until                         TEXT                  Latest publication /   │
│                                                       trading date           │
│                                                       (YYYY-MM-DD).          │
│ --horizon-days                  INTEGER RANGE [x>=1]  Forward return horizon │
│                                                       in calendar days       │
│                                                       (default 1 =           │
│                                                       next-day).             │
│                                                       [default: 1]           │
│ --min-articles-per-day          INTEGER RANGE [x>=1]  Drop days with fewer   │
│                                                       matching articles      │
│                                                       (noise filter; default │
│                                                       3).                    │
│                                                       [default: 3]           │
│ --json                                                Emit machine-readable  │
│                                                       JSON instead of human  │
│                                                       table.                 │
│ --config                        PATH                  Watchlist path.        │
│                                                       [default:              │
│                                                       /Users/michaeltookes/… │
│                                                       Projects/genkei-capit… │
│ --help                                                Show this message and  │
│                                                       exit.                  │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

_Illustrative — the example invocation needs lake data/args not available at capture time. Run it against your lake:_

```console
genkei news-sentiment
genkei news-sentiment --json
```

## See also

[`news`](news.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
