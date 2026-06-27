# `genkei query`

Ad-hoc read-only SQL escape hatch (timeout + row cap enforced).

## Options

```text
Usage: python -m genkei.cli query [OPTIONS] [SQL]                              
                                                                                
 Ad-hoc SQL escape hatch (read-only, timeout + row cap enforced).               
                                                                                
╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   sql      [SQL]  Positional SQL string. Mutually exclusive with --file. For │
│                   non-trivial SQL (string literals, multi-line, embedded     │
│                   quotes) prefer --file — shell-quote escaping triple-nested │
│                   (bash → python → SQL) gets ugly fast.                      │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --file                   PATH                  Read SQL from this path       │
│                                                instead of positional arg.    │
│ --limit                  INTEGER RANGE [x>=1]  Server-side row cap wrapped   │
│                                                around your query. Default    │
│                                                100, max 100000.              │
│                                                [default: 100]                │
│ --timeout-seconds        INTEGER RANGE [x>=1]  Postgres statement_timeout in │
│                                                seconds. Default 30, max 300. │
│                                                [default: 30]                 │
│ --format                 TEXT                  Output format: table          │
│                                                (default) | json | csv.       │
│                                                [default: table]              │
│ --json                                         Shortcut for --format json    │
│                                                (matches the convention in    │
│                                                other subcommands).           │
│ --help                                         Show this message and exit.   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei query "SELECT count(*) AS rows FROM coingecko.market_data"
rows
----
8745
(1 row, limit=100)
```

**JSON (`--json`)**

```console
$ genkei query "SELECT count(*) AS rows FROM coingecko.market_data" --json
[
  {
    "rows": 8745
  }
]
```

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
