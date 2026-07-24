"""``genkei``-as-MCP server (B-130) — the cockpit keystone.

Wraps the existing ``genkei`` CLI as a Model Context Protocol (MCP) server
so the *same* tool surface feeds both Claude Code today and the future
cockpit (E-002). This is immediately useful in Claude Code independent of
any UI: register the server and the agent gets typed ``prices`` / ``signals``
/ ``tvl`` / ``query`` / … tools instead of hand-composing Bash.

Subprocess vs import — the recorded decision (B-130 acceptance criterion)
========================================================================
**We shell the ``genkei`` CLI as a subprocess with ``--json``, not import
the ``genkei`` Python modules directly.** Rationale:

* **Fidelity to the locked architecture.** CLAUDE.md locks "the CLI is the
  agent's data interface." Shelling the CLI is the most faithful realization
  of that decision — the MCP tool surface *is* the CLI surface, byte-for-byte
  identical to what Claude Code already gets via Bash. There is one contract,
  not two.
* **Truly thin adapter, zero data-logic duplication.** The adapter builds an
  argv, runs the process, and returns stdout. It reaches into no internals,
  re-implements no arg parsing, and re-serializes nothing. Every fix to a
  subcommand (a new column, a bug fix, a freshness tweak) flows to the MCP
  surface for free.
* **Stable, documented boundary.** Every subcommand already supports
  ``--json`` and serializes via the shared ``_helpers.json_default``
  (Decimal→str, dates→ISO). Freshness warnings go to *stderr* as parseable
  JSON, so capturing stdout alone gives clean tool output. ``genkei query``
  enforces its read-only / timeout / row-cap / multi-statement guards inside
  the engine, so exposing it via subprocess inherits every guarantee — the
  MCP path cannot weaken them.
* **Isolation.** A subcommand crash is a non-zero exit code + stderr, not an
  exception that can take down the long-lived server process.

The cost is subprocess spawn overhead plus parsing stdout JSON. For an
interactive research tool surface that is negligible, and it buys a hard
guarantee that the MCP client and a human at a Bash prompt see exactly the
same bytes. Importing modules would be marginally faster but couples the
adapter to internals *below* the stable CLI layer and forces re-implementing
Typer's option handling — precisely the duplication the acceptance criteria
forbid.

Package layout
==============
* ``registry`` — declarative ``ToolSpec`` list (subcommand path → description
  → typed params). SDK-free and the single place to add/extend a tool.
* ``adapter`` — translates a ``(ToolSpec, arguments)`` pair into a ``genkei``
  argv, runs it as a subprocess, returns stdout. SDK-free and fully testable
  offline (the subprocess call is the seam tests stub).
* ``server`` — the MCP server proper. Lazily imports the ``mcp`` SDK inside
  ``serve()`` (mirroring how ``genkei.common.notebook`` imports pandas
  lazily), so ``registry`` + ``adapter`` import and test without the SDK
  installed. Console entry point: ``genkei-mcp``.

Install with the extra: ``pip install -e ".[mcp]"`` (needs Python >=3.10 —
the SDK's floor; the CLI itself still runs under the repo's 3.9 harness).
"""

from __future__ import annotations

from genkei.mcp.registry import TOOL_SPECS, ToolParam, ToolSpec, tool_by_name

__all__ = ["TOOL_SPECS", "ToolParam", "ToolSpec", "tool_by_name"]
