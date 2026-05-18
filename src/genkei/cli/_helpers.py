"""Shared helpers for CLI subcommands.

Single source of truth for the small utilities every ``genkei`` subcommand
needs — JSON serialization that preserves Decimal precision, and the
``--since`` / ``--until`` date parsing used by most commands.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

import typer


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
