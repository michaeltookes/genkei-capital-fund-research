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

## Result caching (B-046)

Repeated identical queries in a session are served from a disk-backed cache instead of re-hitting Postgres. The cache key is `(database namespace, SQL, --limit, format)` — any change in those is a fresh query. `--timeout-seconds` is deliberately **not** part of the key (it can only change whether a query errors, and errors are never cached).

Why disk-backed and not in-memory: the CLI is one-shot per process, so each `genkei query` is a fresh interpreter — an in-memory cache could never see the previous invocation. The value the backlog wanted ("the agent issues the same query many times in a session") is many *separate* processes, so the cache lives on disk (see `genkei.common.cache`). A short default TTL (5 min) bounds staleness against the lake's daily-cron refresh.

```console
$ genkei query "SELECT count(*) FROM yahoo.candles"   # 1st: hits DB, caches
$ genkei query "SELECT count(*) FROM yahoo.candles"   # 2nd: served from cache
$ genkei query "SELECT count(*) FROM yahoo.candles" --no-cache      # force fresh
$ genkei query "SELECT now()" --cache-ttl 30           # 30s freshness window
```

- `--no-cache` — bypass entirely: fresh DB read, and does **not** populate the cache.
- `--cache-ttl SECONDS` — freshness window; `0` (default) uses `GENKEI_CACHE_TTL` or 300s.
- Cache location: `GENKEI_CACHE_DIR`, else `$XDG_CACHE_HOME/genkei/query` (or `~/.cache/genkei/query`).

Errors are never cached, so a transient failure can't poison a later run.

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
