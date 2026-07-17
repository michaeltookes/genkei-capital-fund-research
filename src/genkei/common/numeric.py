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
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    except Exception:  # pragma: no cover - defensive
        label = field if field is not None else "value"
        LOGGER.warning(
            "safe_decimal: unexpected error coercing %s=%r to Decimal",
            label,
            value,
            exc_info=True,
        )
        return None
