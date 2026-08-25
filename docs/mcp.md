# `genkei` MCP server (B-130)

Wraps the `genkei` CLI as a [Model Context Protocol](https://modelcontextprotocol.io)
server so the same tool surface plugs into MCP-speaking clients. After the
2026-08-14 E-002 pivot, this is the primary interface path: register the server
and the agent gets typed `prices` / `signals` / `tvl` / `query` / … tools
instead of hand-composing Bash.

## Design: subprocess, not import

The server **shells the `genkei` CLI as a subprocess with `--json`** rather
than importing the `genkei` Python modules directly. This is the recorded
decision the B-130 backlog item demands. Rationale:

- **Fidelity.** CLAUDE.md locks *"the CLI is the agent's data interface."*
  Shelling the CLI makes the MCP tool surface byte-for-byte identical to what
  Claude Code already gets via Bash — one contract, not two.
- **Truly thin adapter.** The adapter builds an argv, runs the process, and
  returns stdout. It reaches into no internals, re-implements no arg parsing,
  and re-serializes nothing — so every subcommand fix flows to the MCP surface
  for free. No data logic is duplicated.
- **Stable boundary.** Every subcommand supports `--json` and serializes via
  the shared `_helpers.json_default` (Decimal→str, dates→ISO). Freshness
  warnings go to **stderr** as parseable JSON, so capturing stdout alone gives
  clean tool output.
- **Safety inheritance.** `genkei query` enforces its read-only / statement-
  timeout / row-cap / multi-statement guards *inside the Postgres engine*.
  Exposing it via subprocess inherits every guarantee — the MCP path cannot
  weaken them, and no tool can inject an arbitrary shell command (the argv is
  built from a fixed registry + typed params, `shell=False`).

The cost is subprocess spawn overhead plus parsing stdout JSON — negligible
for an interactive research tool surface, and worth it for the guarantee that
the MCP client and a human at a Bash prompt see exactly the same bytes.

## Package layout

| Module | Role | Needs the `mcp` SDK? |
|---|---|---|
| `genkei.mcp.registry` | Declarative `ToolSpec` list (subcommand → description → typed params). One-line append to add a tool. | No |
| `genkei.mcp.adapter` | Builds the `genkei` argv, runs it as a subprocess, returns stdout. | No |
| `genkei.mcp.server` | The MCP server proper; lazily imports the SDK inside `serve()`. Console entry `genkei-mcp`. | Yes (lazy) |

The registry + adapter are SDK-free so the core package and the offline test
suite import and run without the extra installed — mirroring how
`genkei.common.notebook` imports pandas lazily. Only the running server needs
the SDK.

## Install (blessed path: `uvx`)

The MCP SDK requires **Python >= 3.10**, but the system Python on a typical Mac
(and this repo's own harness) is 3.9. **The one blessed install method is
[`uv`](https://docs.astral.sh/uv/)'s `uvx`** — it provisions a managed CPython
that satisfies the package's `requires-python = ">=3.10"` automatically, so
there is no venv to create, activate, or keep on `PATH`. The whole run command
is:

```bash
uvx --from '/absolute/path/to/genkei-capital-fund-research[mcp]' genkei-mcp
```

- `--from '<checkout>[mcp]'` builds the private `genkei` package from your local
  checkout **with the `[mcp]` extra**, into a cached ephemeral environment, and
  runs its `genkei-mcp` entry point. First run downloads a Python ≥3.10 and
  builds the wheel (~15 s); subsequent runs are cached and near-instant.
- The package is **not on PyPI** (private, free-sources-only repo), so `--from`
  points at a local path with extras or a PEP 508 VCS requirement — never a bare
  `genkei`. A git form works the same way once you have repo access:
  `uvx --from 'genkei[mcp] @ git+ssh://git@github.com/<owner>/genkei-capital-fund-research' genkei-mcp`.

Install `uv` once (it is the only prerequisite):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # puts uv + uvx in ~/.local/bin
```

**Why `uvx` and not `pipx`.** `pipx` also isolates the tool, but it installs
*into whatever Python it's given* — it will not fetch a 3.10+ interpreter when
the system is on 3.9, so you'd be back to juggling a venv. `uv` fetches the
interpreter itself, which is the entire constraint this item exists to solve.
(If you already run Python ≥3.10 everywhere, `pipx install '<checkout>[mcp]'`
gives a persistent `genkei-mcp` on `PATH` — that's the one-line alternative,
not the blessed path.)

**SDK version pin.** The `[mcp]` extra is capped at `mcp>=1.0,<2`: the server
builds on the low-level `mcp.server.Server` decorator API
(`@server.list_tools()` / `@server.call_tool()`), which the 2.0 SDK removed. An
unpinned floor resolves to 2.x and crashes the server at startup
(`AttributeError: 'Server' object has no attribute 'list_tools'`). Porting the
server to the 2.x handler shape is a tracked follow-up; until then the cap keeps
the one-line install working.

### Dev install (working in the repo)

For hacking on the server itself, the editable install still works under any
Python ≥3.10 venv:

```bash
pip install -e ".[mcp]"
```

The `genkei` CLI and the offline test suite run fine under the repo's 3.9
harness; only the running MCP server process needs 3.10+.

## Tools exposed

Each tool passes through its subcommand's `--json` shape. Tool names use
snake_case; the `watchlist` subgroup becomes one tool per action.

| Tool | `genkei` subcommand | Key params |
|---|---|---|
| `prices` | `prices` | `ticker` (req), `source`, `since`, `until`, `limit` |
| `signals` | `signals` | `asset`, `since`, `until`, `top` |
| `tvl` | `tvl` | `chain`, `protocol`, `since`, `until`, `limit` |
| `zcash_usage` | `zcash-usage` | `since`, `until`, `limit`, `by_pool` |
| `macro` | `macro` | `series` (req), `since`, `until`, `limit` |
| `momentum` | `momentum` | `asset`, `asset_class`, `window`, `limit` |
| `anomalies` | `anomalies` | `asset`, `since`, `until`, `limit` |
| `news` | `news` | `asset`, `theme`, `since`, `until`, `limit` |
| `watchlist_list` | `watchlist list` | `sleeve` |
| `watchlist_health` | `watchlist health` | `stale_hours`, `skip_drift` |
| `watchlist_gaps` | `watchlist gaps` | `threshold_hours` |
| `watchlist_score` | `watchlist score` | `ticker`, `sleeve`, `since` |
| `query` | `query` | `sql` (req, positional), `limit`, `timeout_seconds` |

This is the curated B-130 surface (the required tools plus the high-value
macro / momentum / news / anomaly reads). Adding another subcommand later is a
one-line `ToolSpec` append in `genkei.mcp.registry` — the rest generates from
it. Domains without a CLI subcommand yet (on-chain staking, SUI validators,
SUI unlocks — B-136) receive tools once their subcommands land.

### `query` is read-only

The `query` tool exposes the ad-hoc SQL escape hatch. Every safety guard is
enforced by the CLI / Postgres engine, not by the MCP layer, so the MCP path
inherits them unchanged:

- Runs inside a `READ ONLY` Postgres transaction (writes/DDL rejected by the
  engine).
- Server-side `statement_timeout` (default 30s, max 300s).
- Server-side row cap wrapping the query (default 100, max 100 000).
- Multi-statement input (`;` outside literals) rejected at parse time.

## The `.env` / `GENKEI_DATABASE_URL` story (read this before configuring a client)

The spawned `genkei` subprocesses need `GENKEI_DATABASE_URL` to reach the
Beelink Postgres. At startup the server calls `load_env_file()` (B-135), which
reads a `.env` **from the server process's current working directory** — not
from the repo it was installed from. That distinction decides how each client
supplies the URL:

- **cwd is the repo root** → `.env` is auto-discovered; no `env` block needed.
  This is the Claude Code case when you launch `claude` from inside the checkout.
- **cwd is anything else** (an app bundle, your home dir, a different project) →
  `.env` is *not* found. Pass the URL explicitly in the client's `env` block.
  Claude Desktop, Cursor, and Codex all launch the server from their own working
  directory, so they **always** need the `env` block.

Placeholder used below (never commit the real value — it lives only in the
gitignored `.env`, a client config outside the repo, or the parent process
environment):

```
GENKEI_DATABASE_URL=postgresql+psycopg://<user>:<password>@<beelink-host>:5440/<db>
```

Every snippet below launches the same blessed command
(`uvx --from '<checkout>[mcp]' genkei-mcp`); replace `<checkout>` with the
absolute path to your clone.

## Per-client setup

| Client | Config surface | Verified on this Mac? |
|---|---|---|
| Claude Code | `claude mcp add` / `.mcp.json` | ✅ live-verified (`✔ Connected`, 13 tools) |
| Codex CLI | `codex mcp add` → `~/.codex/config.toml` | ✅ registration live-verified (Codex 0.142.0) |
| Claude Desktop | `claude_desktop_config.json` | ⚠️ authored from official docs (not installed here) |
| Cursor | `~/.cursor/mcp.json` | ⚠️ authored from official docs (not installed here) |

### Claude Code — live-verified

The fastest path is `claude mcp add`. Launched from the repo root, the server
finds `.env` on its own, so no secret goes into any config file:

```bash
claude mcp add genkei -s local -- \
  uvx --from '/absolute/path/to/genkei-capital-fund-research[mcp]' genkei-mcp
```

`claude mcp list` then health-checks it (`genkei: … - ✔ Connected`). Scopes:

- `-s local` (default) — stored in `~/.claude.json` under this project; private,
  not committed. **Recommended** for the secret-free, cwd-`.env` setup above.
- `-s user` — available in every project; use if you launch `claude` from
  outside the checkout, and then add the URL explicitly:
  `claude mcp add genkei -s user -e GENKEI_DATABASE_URL='postgresql+psycopg://…' -- uvx --from '<checkout>[mcp]' genkei-mcp`.
- `-s project` — writes a committed `.mcp.json` at the repo root. Only use the
  cwd-`.env` form here; **never** put a real `GENKEI_DATABASE_URL` in `.mcp.json`
  (it's committed). Equivalent JSON:

  ```json
  {
    "mcpServers": {
      "genkei": {
        "command": "uvx",
        "args": ["--from", "/absolute/path/to/genkei-capital-fund-research[mcp]", "genkei-mcp"],
        "env": {}
      }
    }
  }
  ```

### Codex CLI — registration live-verified

```bash
codex mcp add genkei \
  --env GENKEI_DATABASE_URL='postgresql+psycopg://<user>:<password>@<beelink-host>:5440/<db>' \
  -- uvx --from '/absolute/path/to/genkei-capital-fund-research[mcp]' genkei-mcp
```

Codex writes an `[mcp_servers.genkei]` block to `~/.codex/config.toml` and masks
the env value as `*****` in `codex mcp get` / `codex mcp list` output. Codex
launches the server from its own cwd, so the `--env` passthrough is required.
`codex mcp list` shows the server `enabled` (it does not run a live connection
health-check the way Claude Code does).

### Claude Desktop — authored from docs

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` and
restart the app. Claude Desktop launches the server from the app bundle, so the
`env` block is required:

```json
{
  "mcpServers": {
    "genkei": {
      "command": "uvx",
      "args": ["--from", "/absolute/path/to/genkei-capital-fund-research[mcp]", "genkei-mcp"],
      "env": {
        "GENKEI_DATABASE_URL": "postgresql+psycopg://<user>:<password>@<beelink-host>:5440/<db>"
      }
    }
  }
}
```

If `uvx` isn't found (Claude Desktop's `PATH` may not include `~/.local/bin`),
use its absolute path — `command: "/Users/<you>/.local/bin/uvx"`.

### Cursor — authored from docs

Create `~/.cursor/mcp.json` for global registration. If you choose the
project-local `.cursor/mcp.json`, keep it untracked (this repo's `.gitignore`
excludes it) and use `${env:GENKEI_DATABASE_URL}` so credentials stay outside
the checkout:

```json
{
  "mcpServers": {
    "genkei": {
      "command": "uvx",
      "args": ["--from", "/absolute/path/to/genkei-capital-fund-research[mcp]", "genkei-mcp"],
      "env": {
        "GENKEI_DATABASE_URL": "${env:GENKEI_DATABASE_URL}"
      }
    }
  }
}
```

Once registered, the tools appear to the agent as `genkei` MCP tools in any of
these clients.

## Network reality (stdio-local only; remote is a follow-up)

The server speaks MCP **over stdio** — the transport every client above uses.
The subprocess it spawns connects straight to the Beelink Postgres, so the
machine running the client **must be able to reach that Postgres** (same LAN, or
over Tailscale). A laptop off-network, `claude.ai` in a browser, or a phone
cannot use this stdio setup.

Reaching those truly remote clients needs a **streamable-HTTP transport plus an
auth story**, and that is **explicitly out of scope for this item (v1)**:
exposing the lake over HTTP reopens the B-137 exposure posture (currently
LAN-only, no public route) and must not ship without authentication designed
first. It is scoped as a follow-up decision, not built here.
