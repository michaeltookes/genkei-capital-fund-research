"""Shared helpers for CLI subcommands.

Single source of truth for the small utilities every ``genkei`` subcommand
needs — JSON serialization that preserves Decimal precision, and the
``--since`` / ``--until`` date parsing used by most commands.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Optional

import typer

from genkei.common.freshness import stale_banner


def emit_freshness_warning(freshness: Optional[dict[str, Any]], *, json_out: bool) -> None:
    """Surface a stale-data warning on **stderr** (B-023). No-op when fresh.

    Deliberately stderr-only so it never corrupts captured ``--json`` stdout
    (the reflection cycle and other consumers parse ``genkei prices --json``
    as a bare row list). In JSON mode the warning is itself a structured
    ``{"freshness": {...}}`` object so an agent can capture stderr and branch
    on ``stale`` / ``age_hours``; in human mode it's a one-line banner.
    """
    if not freshness or not freshness.get("stale"):
        return
    if json_out:
        typer.echo(json.dumps({"freshness": freshness}, default=json_default), err=True)
    else:
        typer.echo(stale_banner(freshness), err=True)


def json_default(value: Any) -> Any:
    """``json.dumps`` ``default=`` hook for Postgres-shaped query results.

    Decimal → string (preserves precision; never lossy). Anything with an
    ``isoformat`` method (date, datetime, time) → its ISO 8601 form.
    """
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def parse_date(raw: Optional[str], *, label: str) -> Optional[date]:
    """Parse a YYYY-MM-DD CLI argument; ``None`` passes through.

    Raises ``typer.BadParameter`` with the option label so users see
    ``Invalid value for --since: ...`` rather than a stack trace.
    """
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--{label} must be YYYY-MM-DD: {raw}") from exc
