# Genkei Capital Fund Research

Genkei Capital research-desk: a queryable financial-data lake (equities + crypto) that backs experiments, trend analysis, inefficiency detection, and an on-demand AI researcher. See `CLAUDE.md` for the full project framing and `docs/backlog.md` for what's planned.

A DeFiLlama-only MVP under `scripts/` ships today (BTC/ETH/SOL/LINK/SUI focus, daily brief). It will be refactored onto Postgres in Phase 1 and become one ingester among many.

## Setup

### 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"
```

Requires Python 3.10+.

### 2. Environment variables

Copy `.env.example` to `.env` and fill in values. `.env` is gitignored.

```bash
cp .env.example .env
$EDITOR .env
```

Required:

| Variable | Used by | Source |
|---|---|---|
| `GENKEI_DATABASE_URL` | Every Postgres helper, every ingester, Alembic migrations | Homelab Postgres (see `docs/infrastructure.md`) |
| `COINGECKO_API_KEY` | CoinGecko Demo ingester; required for authenticated Demo API requests | https://www.coingecko.com/en/api/pricing |

Optional (uncomment in `.env` when the corresponding ingester lands):

| Variable | Used by | Source |
|---|---|---|
| `FRED_API_KEY` | FRED ingester (B-028) | https://fred.stlouisfed.org/docs/api/api_key.html |
| `BEA_API_KEY` | BEA ingester (B-029) | https://apps.bea.gov/API/signup/ |
| `EIA_API_KEY` | EIA ingester (B-032) | https://www.eia.gov/opendata/register.php |

**Loading the file** — three options, pick whichever your shell prefers:

- One-shot: `set -a; source .env; set +a` before running anything.
- Persistent: `direnv` — keep secrets only in gitignored `.env`, create a minimal `.envrc` containing `dotenv .env`, then run `direnv allow`. Do not paste full secrets into `.envrc`.
- From Python: `from genkei.common import load_env_file; load_env_file()` at the top of an entry point. Existing env vars take precedence; safe to call unconditionally.

### 3. GitHub Actions secrets

CI reads each variable from a same-named GitHub Actions secret instead of `.env`. To add or update:

```bash
gh secret set GENKEI_DATABASE_URL          # paste value when prompted
gh secret list
```

Workflows reference secrets via `${{ secrets.GENKEI_DATABASE_URL }}` in YAML.

> **Note:** GitHub-hosted runners can't reach the homelab Postgres directly (private LAN, double NAT). When CI starts needing real-Postgres tests, we move to a self-hosted runner on the Beelink — see B-077 in the backlog.

## Scope

The DeFiLlama MVP uses only public DeFiLlama APIs. New ingesters (Phase 2) target free/open sources too — paid APIs are deferred until a private-data story exists. Non-target assets are ignored unless they provide ecosystem context for the focused assets.

Bitcoin-adjacent chains/projects exposed by DeFiLlama are grouped under **Bitcoin
ecosystem** when they match configured labels such as Lightning, Stacks, Rootstock/RSK,
Babylon, Botanix, Merlin, Bitlayer, BOB, or equivalents in
`config/defillama.sources.json`.

## Repository layout

```text
config/defillama.sources.json      DeFiLlama source and asset scope config
scripts/collect_defillama.py       Collect raw public API snapshots
scripts/normalize_defillama.py     Normalize raw snapshots into focused daily JSON
scripts/build_daily_report.py      Build analyst-style Markdown daily brief
docs/defillama-mvp.md              Design notes and operating boundaries
docs/defillama-daily-review.md     Review checklist and acceptance standard
.github/workflows/defillama-daily.yml Scheduled/manual daily brief builder
data/raw/defillama/.gitkeep        Raw artifact directory anchor
data/normalized/defillama/.gitkeep Normalized artifact directory anchor
reports/daily/.gitkeep             Generated daily report directory anchor
tests/                             Offline unit tests
```

Generated raw JSON, normalized JSON, JSONL, and daily Markdown reports are ignored by git.
Only directory anchors are tracked.

## Run the pipeline

From the repository root:

```bash
python3 scripts/collect_defillama.py
python3 scripts/normalize_defillama.py
python3 scripts/build_daily_report.py
```

Outputs:

- raw snapshots: `data/raw/defillama/<timestamp>/`
- normalized daily dataset: `data/normalized/defillama/daily-YYYY-MM-DD.json`
- analyst brief: `reports/daily/defillama-daily-YYYY-MM-DD.md`

## Validate locally

```bash
python3 -m unittest discover -s tests
python3 -m compileall scripts tests
```

Unit tests are deterministic and do not hit the network.

## Scheduled run

`.github/workflows/defillama-daily.yml` runs daily at 12:15 UTC and can also be started
manually with `workflow_dispatch`. The workflow validates tests, collects public
DeFiLlama data, normalizes it, builds the analyst brief, and uploads the generated
Markdown/normalized JSON as workflow artifacts. It does **not** commit generated raw,
normalized, or report artifacts.

Before using a daily brief for decision support, apply
`docs/defillama-daily-review.md`.
