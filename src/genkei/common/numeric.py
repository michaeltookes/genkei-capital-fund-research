"""Shared numeric coercion helpers."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

LOGGER = logging.getLogger(__name__)


def safe_decimal(value: Any, *, field: str | None = None) -> Decimal | None:
    """Return ``Decimal(str(value))`` or ``None`` for ordinary parse failures.

    Unexpected exceptions are logged because unattended ingest should surface
    surprises instead of silently dropping malformed values.
    """
    try:
        text = str(value)
    except Exception:
        _log_unexpected_decimal_error(value, field=field)
        return None

    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    except Exception:  # pragma: no cover - defensive
        _log_unexpected_decimal_error(value, field=field)
        return None


def _log_unexpected_decimal_error(value: Any, *, field: str | None) -> None:
    label = field if field is not None else "value"
    LOGGER.warning(
        "safe_decimal: unexpected error coercing %s=%s to Decimal",
        label,
        _safe_repr(value),
        exc_info=True,
    )


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"
