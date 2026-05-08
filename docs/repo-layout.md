# Repo layout decision

**Date:** 2026-05-07 · **Resolves:** B-013 (decision) · **Migration:** lands in Phase 1 (B-017+)

## Decision

Adopt the **`src/` layout** with a single top-level package `genkei`:

```text
src/
  genkei/
    __init__.py
    common/        # shared HTTP client, Postgres helpers, config loader
    ingest/        # one module per data source (defillama, sec, fred, ...)
    normalize/     # one module per source — raw -> Postgres rows
    cli/           # subcommand-per-domain CLI (Phase 3)
    experiments/   # notebook-callable helpers
    reports/       # markdown rendering (DefiLlama daily, future briefs)
tests/
  common/
  ingest/
  normalize/
  cli/
  reports/
```

Tests mirror the package shape under `tests/`. The CLI is shipped as a console script via `pyproject.toml` (`genkei = "genkei.cli:main"`).

## Tradeoffs considered

| | Flat `scripts/` (current) | `src/genkei/{...}/` (chosen) |
|---|---|---|
| Friction to add a one-off script | Low — just drop a file in | Higher — choose a subpackage, add `__init__.py` |
| Separation of concerns | None — every script in one folder | Explicit: ingest vs normalize vs cli vs reports |
| Shared code (HTTP client, DB helpers) | Awkward — no natural home | Lives in `common/`, imported everywhere |
| Test imports | Path manipulation per file | Clean: `from genkei.ingest.defillama import ...` |
| Installable CLI | Requires kludge (entry-point script) | Native via `pyproject.toml` console scripts |
| Catches install/packaging bugs early | No — tests run against repo root | **Yes** — `src/` layout forces tests to import from installed package |
| Scales to 10+ ingesters | Painful | Clean |
| Onboarding cost for casual contributors | Lower | Slightly higher (one more concept) |

## Rationale

The CLI is the **primary user-facing artifact** (CLAUDE.md: "Agent composes CLI invocations via Bash"). It must be installable as a real binary — `genkei` on `$PATH`. That alone requires a Python package, which makes `src/` the right shape.

Beyond that, the project has 10+ data sources, an experiments framework, and a reports module — five distinct concerns. Burying them in a flat `scripts/` directory would create one of the two failure modes that the project is built to avoid: ambiguity about where shared code lives. The `src/genkei/` layout makes the boundaries explicit and matches the way the agent and humans both think about the system (ingest vs query vs experiment vs report).

The `src/` (vs `genkei/` at repo root) choice is deliberate: it forces tests to import from the **installed** package rather than the source tree, which catches packaging bugs that bite later when the CLI is `pip install`'d on the homelab Beelink runner or in a Routine sandbox.

## Migration plan (Phase 1)

Carried out as part of B-017+ when DefiLlama is refactored onto Postgres. **Not done in this mission.**

| Current | Target |
|---|---|
| `scripts/__init__.py` | (deleted; superseded by `src/genkei/__init__.py`) |
| `scripts/collect_defillama.py` | `src/genkei/ingest/defillama.py` |
| `scripts/normalize_defillama.py` | `src/genkei/normalize/defillama.py` |
| `scripts/build_daily_report.py` | `src/genkei/reports/defillama_daily.py` |
| `tests/test_collect_defillama.py` | `tests/ingest/test_defillama.py` |
| `tests/test_normalize_defillama.py` | `tests/normalize/test_defillama.py` |
| `tests/test_build_daily_report.py` | `tests/reports/test_defillama_daily.py` |
| `config/defillama.sources.json` | unchanged (configs stay at repo root under `config/`) |

`.github/workflows/defillama-daily.yml` updates to invoke the CLI: `python -m genkei ingest defillama` (or `genkei ingest defillama` once installed) instead of `python scripts/collect_defillama.py`.

## Implications

- **B-012 (`pyproject.toml`)** is now a hard prerequisite for the migration. Use `hatchling` as the build backend (modern, no setup.py); declare the package as `src/genkei/`; expose the console script entry point.
- **Phase 3 CLI** subcommands live under `src/genkei/cli/` and are wired to the `genkei` entry point. Each subcommand module follows the same shape (typed args, `--json` flag, human output by default).
- **Phase 5 experiments** notebooks `import genkei.ingest.defillama` cleanly — no `sys.path` hacks.
- **Test discovery**: CI runs `pip install -e .[dev]` first, then `python -m unittest discover -s tests`. Local dev loop is the same (no PYTHONPATH manipulation).
- **Mission queue, configs, docs, GH workflows** stay at repo root — they're project artifacts, not Python code.
