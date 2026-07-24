"""MCP server exposing the ``genkei`` CLI as tools (B-130).

Turns the declarative :data:`~genkei.mcp.registry.TOOL_SPECS` into MCP tool
definitions and routes each ``call_tool`` through
:func:`genkei.mcp.adapter.run_tool`, which shells the CLI as a subprocess.

The ``mcp`` SDK is imported **lazily inside** :func:`serve` (and the small
schema helper below is SDK-free) so that :mod:`genkei.mcp.registry` and
:mod:`genkei.mcp.adapter` — the tested surface — import without the SDK
installed. This mirrors ``genkei.common.notebook``'s lazy pandas import: the
core package and the offline test suite must run without the optional extra.
Install the server's dependency with ``pip install -e ".[mcp]"`` (Python
>=3.10).

Console entry point: ``genkei-mcp`` (see ``pyproject.toml`` ``[project.scripts]``).
It speaks MCP over stdio — the transport Claude Code and the cockpit both use.
"""

from __future__ import annotations

import asyncio
from typing import Any

from genkei.mcp.registry import TOOL_SPECS, ToolSpec

SERVER_NAME = "genkei"

# JSON-schema ``type`` per our ParamType. Kept SDK-free so the schema builder
# is unit-testable without ``mcp`` installed.
_JSON_SCHEMA_TYPE = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}


def build_input_schema(spec: ToolSpec) -> dict[str, Any]:
    """Render a tool's params into a JSON-Schema object for MCP ``inputSchema``.

    Pure data → data; no SDK dependency. Exposed (and tested) independently so
    the subcommand→tool mapping is pinned without needing the ``mcp`` package.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in spec.params:
        prop: dict[str, Any] = {"type": _JSON_SCHEMA_TYPE[param.type]}
        if param.description:
            prop["description"] = param.description
        properties[param.name] = prop
        if param.required:
            required.append(param.name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        # No extra keys — a stray argument is a caller bug, and the adapter
        # rejects unknowns loudly rather than silently dropping them.
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def serve() -> None:
    """Run the MCP server over stdio until the client disconnects.

    Lazily imports the ``mcp`` SDK so the rest of the package stays import-safe
    without the extra. Bootstraps the environment the same way the CLI does —
    via ``genkei.cli.main``'s ``load_env_file`` — except here we load it once
    at server startup so the spawned CLI subprocesses inherit a populated
    environment even when the MCP client launched us without sourcing ``.env``
    (B-135).
    """
    from genkei.common import load_env_file

    # Populate os.environ from repo-root/cwd .env before any subprocess spawns.
    # The subprocesses inherit this process's environment, so loading here means
    # each `genkei` child resolves GENKEI_DATABASE_URL without its own source.
    load_env_file()

    asyncio.run(_run_stdio())


async def _run_stdio() -> None:
    """Async entry point — build the server and pump stdio."""
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    from genkei.mcp.adapter import ToolInvocationError, run_tool
    from genkei.mcp.registry import tool_by_name

    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=build_input_schema(spec),
            )
            for spec in TOOL_SPECS
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        try:
            spec = tool_by_name(name)
        except KeyError:
            return [types.TextContent(type="text", text=f"unknown tool: {name!r}")]

        try:
            # The CLI subprocess is blocking; run it off the event loop so a
            # slow query doesn't stall the server.
            result = await asyncio.to_thread(run_tool, spec, arguments or {})
        except ToolInvocationError as exc:
            return [types.TextContent(type="text", text=f"invalid tool call: {exc}")]

        if result.ok:
            # stdout is the machine-readable payload (JSON for --json tools).
            # Freshness warnings on stderr are informational; append them so
            # the agent sees staleness without them polluting the JSON body.
            text = result.stdout.rstrip("\n") or "(no output)"
            if result.stderr.strip():
                text = f"{text}\n\n[stderr]\n{result.stderr.rstrip()}"
            return [types.TextContent(type="text", text=text)]

        # Non-zero exit: surface stderr (and any stdout) so the agent can
        # correct the call. The CLI already renders clean, agent-readable
        # error lines for user errors (bad ticker, SQL error, …).
        err = result.stderr.strip() or result.stdout.strip() or "(no error output)"
        return [
            types.TextContent(
                type="text",
                text=f"genkei {' '.join(spec.subcommand)} failed (exit {result.exit_code}): {err}",
            )
        ]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> int:
    """Console-script entry point for ``genkei-mcp``."""
    serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
