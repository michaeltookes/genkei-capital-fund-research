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

## Install

The MCP SDK is an **optional extra** (kept out of core deps, like `[notebooks]`):

```bash
pip install -e ".[mcp]"
```

The SDK requires **Python >= 3.10**. The `genkei` CLI itself still runs under
the repo's 3.9 harness; only the MCP server process needs 3.10+.

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

## Register in Claude Code

### Option A — `.mcp.json` (project-scoped, checked in or local)

Add a `.mcp.json` at the repo root (or your Claude Code config). Adjust the
`command` to the interpreter that has the `[mcp]` extra installed:

```json
{
  "mcpServers": {
    "genkei": {
      "command": "genkei-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

If `genkei-mcp` isn't on `PATH`, point at the venv explicitly:

```json
{
  "mcpServers": {
    "genkei": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "genkei.mcp.server"],
      "env": {}
    }
  }
}
```

The server bootstraps the environment itself: at startup it loads a
repo-root/cwd `.env` (B-135) so the spawned `genkei` subprocesses resolve
`GENKEI_DATABASE_URL` without a sourced shell. If you prefer to pass secrets
explicitly, set them in the `env` block instead.

### Option B — `claude mcp add`

```bash
claude mcp add genkei -- genkei-mcp
```

or, pinning the venv interpreter:

```bash
claude mcp add genkei -- /absolute/path/to/.venv/bin/python -m genkei.mcp.server
```

Once registered, the tools appear to the agent as `genkei` MCP tools. The
server speaks MCP over stdio — the transport Claude Code and other local MCP
clients use. Truly remote clients need a separate streamable-HTTP transport and
auth story before they come into scope (tracked by B-142).
