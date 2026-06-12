# Research

The append-only investment-research workspace. Every meaningful research session ends with a decision file in `decisions/`. Periodically, `/reflect-decisions` walks decisions past their horizon and updates them with realized outcomes.

## Layout

```
docs/research/
├── README.md                  this file
├── decisions/                 append-only decision log
│   ├── _template.md           frontmatter + body skeleton for a new entry
│   └── YYYY-MM-DD-<topic>.md  one file per decision
└── aggregate-YYYY-MM-DD.md    optional — calibration snapshots every ~10 reflections
```

## How a session is supposed to run

1. `/research <question>` (loads `prompts/research-methodology.md` + recent reflections).
2. Work through the methodology (frame → macro → fundamentals → flow → cross-source → counter-thesis → conclusion).
3. Save a new decision file in `decisions/YYYY-MM-DD-<topic>.md` using `_template.md`.
4. Commit it in the same session.

## How the reflection cycle works

1. `/reflect-decisions` (loads `prompts/reflect-on-decisions.md`) — run weekly, or after each new decision lands.
2. For every file in `decisions/` with `status: pending` past its horizon, pull realized prices, compute alpha vs benchmark, append outcome + 2-3 sentence reflection, flip `status: resolved`.
3. Commit one batch per run.

## Frontmatter contract

Every decision file's frontmatter MUST have these keys (validated in CI by `tests/test_research_decisions.py`):

| key | type | values |
|---|---|---|
| `date` | ISO date | `2026-05-17` |
| `asset` | string | ticker (`AAPL`), id (`bitcoin`), series (`DGS10`), or label (`cohort: software`) |
| `sleeve` | string | `equity-core` / `crypto-core` / `crypto-tactical` / `macro-aware` |
| `horizon` | string | `weeks` / `months` / `years` |
| `confidence` | string | `low` / `medium` / `high` |
| `status` | string | `pending` (default), `resolved` (after reflection), `deferred` (missing data source) |
| `trigger_reassessment` | string | one-line description of the observation that would flip the call before horizon |

Add optional keys freely (`tags`, `related`, etc.). The contract is the minimum.

## Keeping prompts in sync with the CLI

The methodology prompts ARE the researcher's program — a stale claim in them produces wrong behavior in every future session, silently. (Real example: after B-092 shipped equity prices, the reflect prompt still said "equity prices aren't ingested," which would have terminally `deferred` every equity decision out of the reflection cycle.)

**When a PR ships a new `genkei` subcommand, retires one, or flips a table EMPTY → populated, that PR must also grep the agent-facing docs for claims it invalidates:**

```bash
grep -rn "<command-or-table-name>" prompts/ .claude/skills/
```

Files to check: `prompts/research-methodology.md`, `prompts/reflect-on-decisions.md`, `.claude/skills/research/SKILL.md`, `.claude/skills/reflect-decisions/SKILL.md`. Phrases like "not yet ingested", "no typed surface yet", "currently EMPTY" are dated the moment they're written — qualify them with the backlog item that will obsolete them, and remove them when it ships.

## Why this exists

CLAUDE.md frames this project as a queryable data lake powering four use cases — the fourth (on-demand AI researcher) only pays off if the research it produces is *durable*. The decision log + reflection cycle is what makes it durable: every decision becomes data the next decision draws on. Pattern recognition over our own track record is the only path to spotting systematic biases (over-confidence on one sleeve, under-weighting macro, etc.).

Three patterns borrowed from TradingAgents (D-018 in `docs/architecture.md`) drive this:
1. Structured research methodology (`prompts/research-methodology.md`).
2. Append-only decision log + outcome reflection (`prompts/reflect-on-decisions.md`).
3. Two-phase analysis → risk separation (Phase A neutral assembly, Phase B counter-thesis) within the methodology.

The multi-agent framework + LangGraph orchestration + per-run live API fetching that TradingAgents wraps these patterns in were explicitly NOT adopted; Claude Code is the harness (D-017), and the lake replaces per-run fetches.
