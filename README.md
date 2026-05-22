# Genkei Capital Fund Research

A queryable financial-data lake (equities + crypto) and an on-demand AI researcher built on top of it. The lake is the asset; daily briefs, signals, and research sessions are emergent UIs over it.

Operating *as if* a real fund — data hygiene, archival, audit trail at fund-grade. Actual capital is personal + close friends/family; no fiduciary duty today, but outputs must be defensible if scope expands. See `CLAUDE.md` for the full project framing.

## The loop

```text
   ┌──────────────────┐        ┌──────────────────┐        ┌─────────────────────┐
   │  External APIs   │        │     Postgres     │        │    `genkei` CLI     │
   │                  │        │   (TimescaleDB)  │        │                     │
   │  DeFiLlama       │  ───►  │                  │  ───►  │  typed subcommands  │
   │  CoinGecko       │  raw   │  meta.raw_blobs  │  read  │  per data domain    │
   │  SEC EDGAR       │  blobs │  per-source      │  only  │                     │
   │  FRED            │        │  schemas         │        │  + SQL escape hatch │
   │  Etherscan       │        │  analytics.*     │        │                     │
   └──────────────────┘        │  derived views   │        └──────────┬──────────┘
            │                  └──────────────────┘                   │
            │ GH Actions (daily)                                      │ Bash composition
            │ self-hosted on Beelink                                  ▼
            ▼                                              ┌─────────────────────┐
   ┌──────────────────┐                                    │   Agent (Claude)    │
   │  Collectors      │                                    │                     │
   │  src/genkei/     │                                    │  /research → walks  │
   │   ingest/*       │                                    │  methodology;       │
   │                  │  ───►  meta.raw_blobs              │  /reflect-decisions │
   │  Normalizers     │  ───►  per-source tables           │  closes the loop    │
   │   normalize/*    │                                    │                     │
   └──────────────────┘                                    │  decision-log:      │
                                                          │  docs/research/      │
                                                          └─────────────────────┘
```

Three ways the lake gets used:
- **Synchronous pairing** — local Claude Code, weekends, ad-hoc Q&A
- **Async overnight** — mission queue at `missions/pending/`, picked up by the `run-missions` skill
- **Scheduled ingest** — GitHub Actions daily crons on a self-hosted runner

## Quickstart

```bash
# 1. Install
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"

# 2. Activate the virtualenv
source .venv/bin/activate

# 3. Configure secrets
cp .env.example .env
$EDITOR .env                   # at minimum: GENKEI_DATABASE_URL

# 4. Load .env into this shell
set -a
source .env
set +a

# 5. Apply migrations
alembic upgrade head

# 6. Sanity check
genkei watchlist health        # OK status across every primary table
genkei prices --ticker BTC     # latest BTC price from the lake
genkei revenue-divergence      # protocol fundamentals vs token price
genkei relative-strength       # crypto peer outperformance @ 30d
```

Python 3.10+. See `docs/infrastructure.md` for the homelab Postgres connection specs.

## What's in the lake

| Source | Schema | What | Coverage |
|---|---|---|---|
| **DeFiLlama** | `defillama.*` | Chain TVL, per-protocol TVL, per-protocol fees + revenue, stablecoin supply per chain, asset prices | 5 hypertables, 8.5y stablecoin history, 7y+ chain TVL |
| **CoinGecko** | `coingecko.*` | Per-coin metadata + daily OHLC market data | 19 coins (7 crypto-core + 12 protocol tokens via B-091) |
| **SEC EDGAR** | `sec.*` | Filings index, XBRL company facts, Form 4 insider transactions | 28 watchlist equities; ~192k Form 4 rows; ~442k XBRL facts |
| **FRED** | `fred.*` | Macro series observations (vintage-aware) | 20 starter series; revision history preserved |
| **On-chain (Etherscan)** | `onchain.staking_events` | Chainlink v0.2 staking pool flow | Gated on `ETHERSCAN_API_KEY` |
| **Analytics** | `analytics.crypto_relative_strength` | Derived view: asset vs peer return across 5 windows | 1,900 rows live |

Every fact row carries the provenance trio: `source_endpoint`, `fetched_at`, `ingest_run_id` (FK to `meta.ingest_runs`). See `docs/storage.md` for the schema strategy and `docs/architecture.md` for the per-table reference.

## CLI surface

The `genkei` command is the canonical query layer — Bash-composable, `--json` everywhere for agent consumption.

| Command | What it answers |
|---|---|
| `genkei prices --ticker BTC` | Crypto price + market cap series |
| `genkei filings --ticker AAPL` | SEC filings index; `--concept us-gaap:Revenues` for XBRL facts |
| `genkei tvl --chain Ethereum` | DeFiLlama TVL; `--protocol aave-v3` for per-protocol |
| `genkei macro --series DGS10` | FRED observation series, vintage-aware (`--as-of YYYY-MM-DD`) |
| `genkei insiders --ticker JPM` | Form 4 transactions, filterable by code / direction / window |
| `genkei insider-clusters` | Multi-reporter buy/sell clusters within a configurable window |
| `genkei revenue-divergence` | Protocol revenue vs token price — price-leads-up / -down / aligned |
| `genkei relative-strength --ticker SUI --peer SOL` | Asset vs peer return across 7/30/90/180/365 day windows |
| `genkei watchlist {list,health,gaps}` | Watchlist coverage + source-freshness monitoring |
| `genkei query "SELECT ..."` | Read-only SQL escape hatch (statement-timeout + row-cap enforced) |

`genkei <cmd> --help` for the full option set per command.

## Research workflow

The data lake's fourth use case ("on-demand AI researcher") runs through a disciplined methodology — checklist, decision log, reflection cycle.

```text
/research <question>     → loads prompts/research-methodology.md
                         → walks frame → macro → fundamentals → flow →
                           cross-source (Phase A) → counter-thesis (Phase B) → conclusion
                         → appends a decision file to docs/research/decisions/

/reflect-decisions       → walks decisions past their horizon
                         → pulls realized prices, computes alpha vs benchmark
                         → appends an Outcome block, flips status pending → resolved
```

Every decision file's frontmatter is validated in CI (`tests/test_research_decisions.py`) — `date`, `asset`, `sleeve`, `horizon`, `confidence`, `status`, `trigger_reassessment`. See `docs/research/README.md` for the contract and `docs/research/decisions/` for live examples.

## How it runs

| Mode | Mechanism | Use case |
|---|---|---|
| Scheduled ingest | GitHub Actions workflows (daily cron) on the Beelink self-hosted runner | Deterministic raw-data pulls; CoinGecko, DefiLlama, FRED, SEC |
| Synchronous pairing | Local Claude Code (this repo's CLAUDE.md) | Weekend sessions, ad-hoc research, design work |
| Async overnight | Mission queue at `missions/pending/` → `run-missions` skill | Long-running tasks that should grind through unattended |

Tests (`python3 -m unittest discover -s tests`) must pass before any push — 599 currently passing, integration tests use `testcontainers[postgres]` so CI exercises real Postgres.

## Repository layout

```text
src/genkei/
├── common/          db.py / http.py / config.py / watchlist.py — shared primitives
├── ingest/          one collector per source (defillama, coingecko, sec, fred, sec_form4, onchain_staking)
├── normalize/       one normalizer per source — raw blobs → per-source tables
├── cli/             Typer-based subcommands; one file per command + _helpers / _watchlist
├── experiments/     Phase 5 detectors (insider_clusters, protocol_revenue, relative_strength)
├── reports/         legacy daily-brief shim (B-025 retired pending)
└── data/            bundled watchlists.yml (single source of truth)

migrations/versions/ Alembic hand-written migrations (no autogen)
docs/                architecture.md / storage.md / infrastructure.md / backlog.md / resolved.md
docs/research/       decision log + methodology
prompts/             research-methodology.md + reflect-on-decisions.md
missions/            pending/ + done/ — async queue
tests/               unit + testcontainers-backed integration
.github/workflows/   tests.yml + per-source daily-* workflows
```

## Conventions

- **Tests** — `python3 -m unittest discover -s tests` must pass before any push. Deterministic + offline by default; integration tests opt in via `testcontainers[postgres]`.
- **Branches** — feature branches, never push to `main`. PRs short: `## Summary` + `## Test plan`.
- **Commit messages** — explain the *why*, not the *what*. AI co-author trailer on auto-generated commits.
- **Backlog hygiene** — `docs/backlog.md` (46 open) + `docs/resolved.md` (47 resolved). Use the `update-backlog` skill after meaningful commits.
- **Secrets** — never in the repo. `.env` (gitignored) locally, GH Actions secrets for CI.
- **Raw vendor data** — never committed. Postgres is the system of record.
- **Decision files** — append-only. Reconsidering a prior call writes a NEW file referencing the old one.

## Deeper docs

| Topic | File |
|---|---|
| Living architecture snapshot + decision log | `docs/architecture.md` |
| Postgres schema strategy + conventions | `docs/storage.md` |
| Homelab Postgres + self-hosted runner | `docs/infrastructure.md` |
| Repo layout conventions | `docs/repo-layout.md` |
| Mission queue format + runner | `docs/missions.md` |
| Research methodology (what `/research` walks) | `prompts/research-methodology.md` |
| Reflection cycle (what `/reflect-decisions` walks) | `prompts/reflect-on-decisions.md` |
| Backlog (open work) | `docs/backlog.md` |
| Resolved (shipped work) | `docs/resolved.md` |
