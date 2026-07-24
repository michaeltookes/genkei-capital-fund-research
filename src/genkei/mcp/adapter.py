"""Subprocess adapter — turn a ``(ToolSpec, arguments)`` pair into a ``genkei``
CLI invocation and return its stdout (B-130).

This is the thin seam between the MCP server and the CLI. It builds an argv
from the tool's declarative param map, always appends ``--json`` (so tool
output is machine-readable), runs the CLI as a **subprocess**, and returns
stdout. Freshness warnings the CLI writes to *stderr* are surfaced separately
so they never corrupt the JSON on stdout.

SDK-free and offline-testable: :func:`run_tool` is the whole surface, and its
one side effect — ``subprocess.run`` — is the seam tests stub. No ``mcp``
import here.

Why ``python -m genkei.cli`` and not the bare ``genkei`` script
--------------------------------------------------------------
We invoke ``[sys.executable, "-m", "genkei.cli", ...]`` rather than shelling a
bare ``genkei`` on ``$PATH``. This guarantees the subprocess runs the *same*
interpreter and installed package as the server — no dependence on a console
script being on ``PATH`` (an MCP client may launch the server from anywhere),
and no ambiguity about which ``genkei`` answers. It is functionally identical
to the ``python3 -m genkei.cli`` form documented in ``genkei/cli/__main__``.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from genkei.mcp.registry import ToolSpec

# How the CLI is launched. Kept as a module constant so tests can assert the
# argv prefix and callers can see the invocation contract at a glance.
CLI_MODULE = "genkei.cli"

# A generous default: `query` can legitimately run up to its own 300s server
# timeout, and health checks touch many tables. The CLI enforces its own
# per-query statement timeout; this is only a backstop against a wedged
# subprocess that never returns.
DEFAULT_TIMEOUT_SECONDS = 320


class ToolInvocationError(RuntimeError):
    """Raised when a required parameter is missing or an unknown one is passed."""


@dataclass(frozen=True)
class ToolResult:
    """Result of running a tool: the CLI's stdout, stderr, and exit code.

    ``ok`` is ``True`` on a zero exit. ``stdout`` is the machine-readable
    payload (JSON when the subcommand supports ``--json``); ``stderr`` carries
    freshness warnings and, on failure, the error line.
    """

    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    argv: tuple[str, ...]


def build_argv(spec: ToolSpec, arguments: dict[str, object]) -> list[str]:
    """Translate ``arguments`` into a ``genkei`` argv for ``spec``.

    Positional params (``flag is None``) are appended in declaration order
    before the flag options. Booleans render as bare flags only when true.
    ``--json`` is appended last when the subcommand supports it. Unknown
    argument keys and missing required params raise
    :class:`ToolInvocationError` so a bad tool call fails loudly rather than
    silently dropping input.
    """
    known = {p.name for p in spec.params}
    unknown = set(arguments) - known
    if unknown:
        raise ToolInvocationError(
            f"tool {spec.name!r} got unknown argument(s): {', '.join(sorted(unknown))}"
        )

    argv: list[str] = [sys.executable, "-m", CLI_MODULE, *spec.subcommand]

    positionals: list[str] = []
    options: list[str] = []
    for param in spec.params:
        if param.name not in arguments:
            if param.required:
                raise ToolInvocationError(f"tool {spec.name!r} requires parameter {param.name!r}")
            continue
        value = arguments[param.name]
        if param.flag is None:
            # Positional argument (e.g. query's SQL string).
            positionals.append(str(value))
            continue
        if param.type == "boolean":
            # Only emit the flag when truthy; a false boolean means "absent".
            if value:
                options.append(param.flag)
            continue
        options.append(param.flag)
        options.append(str(value))

    argv.extend(positionals)
    argv.extend(options)
    if spec.emits_json:
        argv.append("--json")
    return argv


def run_tool(
    spec: ToolSpec,
    arguments: dict[str, object],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ToolResult:
    """Run the ``genkei`` subcommand for ``spec`` and capture its output.

    Delegates entirely to the CLI subprocess — no data logic here. Returns a
    :class:`ToolResult`; the caller (the MCP server) decides how to surface a
    non-zero exit to the MCP client.
    """
    argv = build_argv(spec, arguments)
    completed = subprocess.run(  # noqa: S603 — argv is built from a fixed
        # registry + typed params, never a shell string; shell=False.
        argv,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return ToolResult(
        ok=completed.returncode == 0,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        argv=tuple(argv),
    )
