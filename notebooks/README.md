# Experiments

Reproducible experiments over the Genkei data lake (B-054 / B-055). This is
the "Experiments" use case from `CLAUDE.md` — event studies, signal/return
analyses, regime classifiers — run as **pinned, re-runnable** notebooks rather
than ad-hoc queries that evaporate.

Each experiment is one dated folder under `experiments/`:

```
notebooks/experiments/
  _template/                         ← copy this to start (or use new_experiment)
    experiment.md                    ← hypothesis / method / result / next steps
    experiment.ipynb                 ← the analysis
  2026-07-01-crypto-core-trailing-returns/   ← a worked example
    experiment.md
    experiment.ipynb
    manifest.json                    ← seed + config + pinned ingest-run ids
```

## Setup

The analysis layer (pandas / JupyterLab) is an **optional** dependency group,
kept out of the core package so the CLI and the offline test suite install
without it. Install it once:

```bash
pip install -e ".[notebooks]"
```

The lake connection comes from `GENKEI_DATABASE_URL` (same as the CLI). Load
your `.env` before launching Jupyter so the kernel inherits it:

```bash
set -a; . ./.env; set +a
jupyter lab
```

## Starting a new experiment

One command scaffolds a dated folder from `_template/`:

```python
from genkei.common.notebook import new_experiment
new_experiment("my-hypothesis-slug")   # -> notebooks/experiments/2026-07-01-my-hypothesis-slug/
```

Then open its `experiment.ipynb`, fill in `experiment.md`, and go.

## The reproducibility contract

Two things make an experiment trustworthy months later — *which data it ran
against* and *deterministic sampling*. Both are one line each, near the top of
every notebook:

```python
from genkei.common.notebook import get_session, set_seeds, write_manifest

seed = set_seeds(20260701)                       # deterministic random/numpy
session = get_session()                           # pooled, read-only lake handle
df = session.read_sql_df("""
    SELECT c.product, c.close, c.ingest_run_id AS price_ingest_run_id
    FROM coinbase.candles c
    WHERE c.product = ANY(%s)
""", [["BTC-USD", "ETH-USD"]])
write_manifest(                                   # pins the fact rows you used
    Path("manifest.json"),
    seed=seed,
    config={"window_days": 30, "assets": ["BTC-USD", "ETH-USD", "SOL-USD"]},
    data=df,                                      # extracts *_ingest_run_id columns
    sources=["coinbase"],                         # optional guardrail/filter
)
```

`manifest.json` records the exact `meta.ingest_runs` rows referenced by
`ingest_run_id` columns in the query result. Include `ingest_run_id` (or aliases
ending in `_ingest_run_id`) for every fact table that contributes data. That
lets a later reader trace from result rows to the normalizer run and then to the
raw collector run via `metadata.source_run_id`. Calling
`snapshot_manifest(sources=...)` without result data is still available as a
coarse source-level fallback, but it is not precise enough for rolling-window
experiments.

## Querying

`genkei.common.notebook` wraps the CLI's read-path connection pool. Nothing
here writes: notebook SQL must be a single `SELECT` / `WITH` statement, and the
real Postgres connection is placed in a read-only transaction.

| call | returns | needs pandas |
|---|---|---|
| `session.read_sql_df(sql, params)` | `pandas.DataFrame` | yes |
| `session.read_sql_rows(sql, params)` | `list[dict]` | no |
| `read_sql_df(sql, params)` (module-level) | one-shot `DataFrame` | yes |
| `snapshot_manifest(sources=...)` | pinned run ids | no |

Close the session when done (`session.close()`) or use it as a context
manager. See `_template/experiment.ipynb` for the canonical shape and the
`2026-07-01-crypto-core-trailing-returns` folder for a worked example.
