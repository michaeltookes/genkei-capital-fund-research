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
write_manifest(                                   # pins the snapshot you used
    Path("manifest.json"),
    seed=seed,
    config={"window_days": 30, "assets": ["BTC-USD", "ETH-USD", "SOL-USD"]},
    sources=["coinbase"],                         # omit to pin every source
)
```

`manifest.json` records the latest **successful** `meta.ingest_runs` id per
`(source, endpoint)` at run time — so a later reader can tell whether a re-run
saw the same data or newer data, and can join back to `meta.raw_blobs` for the
exact bytes.

## Querying

`genkei.common.notebook` wraps the CLI's read-path connection pool. Nothing
here writes — every method is a plain `SELECT`.

| call | returns | needs pandas |
|---|---|---|
| `session.read_sql_df(sql, params)` | `pandas.DataFrame` | yes |
| `session.read_sql_rows(sql, params)` | `list[dict]` | no |
| `read_sql_df(sql, params)` (module-level) | one-shot `DataFrame` | yes |
| `snapshot_manifest(sources=...)` | pinned run ids | no |

Close the session when done (`session.close()`) or use it as a context
manager. See `_template/experiment.ipynb` for the canonical shape and the
`2026-07-01-crypto-core-trailing-returns` folder for a worked example.
