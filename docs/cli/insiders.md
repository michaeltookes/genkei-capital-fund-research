# `genkei insiders`

SEC Form 4 insider transactions (--ticker issuer or --reporter-cik).

## Options

```text
Usage: python -m genkei.cli insiders [OPTIONS]                                 
                                                                                
 SEC Form 4 insider transactions (--ticker issuer view or --reporter-cik).      
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --ticker          -t      TEXT                  Equity ticker (issuer view), │
│                                                 e.g. AAPL.                   │
│ --reporter-cik            TEXT                  10-digit SEC CIK of the      │
│                                                 reporting insider (reporter  │
│                                                 view).                       │
│ --code                    TEXT                  SEC transaction code filter  │
│                                                 (P, S, A, F, M, G, etc).     │
│ --derivative                                    Only derivative transactions │
│                                                 (options, warrants).         │
│ --non-derivative                                Only non-derivative          │
│                                                 (open-market) transactions.  │
│ --since                   TEXT                  Start transaction_date       │
│                                                 (YYYY-MM-DD).                │
│ --until                   TEXT                  End transaction_date         │
│                                                 (YYYY-MM-DD).                │
│ --limit                   INTEGER RANGE [x>=1]  Max rows. [default: 30]      │
│ --json                                          Emit machine-readable JSON   │
│                                                 instead of human table.      │
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
$ genkei insiders --ticker AAPL --limit 3
AAPL insider transactions (3 rows, horizon=equity:core:primary)
---------------------------------------------------------------
  date         code          shares      price reporter                     role
  2026-06-16   SD               116     295.14 Borders Ben                  officer(Principal Accounting Officer)
  2026-06-15   MA            30,104        n/a Newstead Jennifer            officer(SVP, GC and Secretary)
  2026-06-15   FD            16,238     296.42 Newstead Jennifer            officer(SVP, GC and Secretary)
```

**JSON (`--json`)**

```console
$ genkei insiders --ticker AAPL --limit 3 --json
[
  {
    "transaction_date": "2026-06-16",
    "transaction_code": "S",
    "acquired_disposed": "D",
    "shares": "116",
    "price_usd": "295.14",
    "post_transaction_shares": "38713",
    "is_derivative": false,
    "security_title": "Common Stock",
    "ownership_type": "D",
    "reporter_name": "Borders Ben",
    "reporter_cik": "0002100523",
    "is_director": null,
... (51 more lines)
```

## See also

[`insider-clusters`](insider-clusters.md) · [`filings`](filings.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
