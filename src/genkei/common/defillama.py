"""Shared DeFiLlama payload helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def as_float(value: Any) -> float | None:
    """Coerce numeric API values to ``float`` while preserving missingness."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_history_timestamp(value: Any) -> datetime | None:
    """Parse DeFiLlama history timestamps (epoch seconds or ISO) as UTC."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            return None
    return None


def stablecoin_supply(balance: Any) -> float | None:
    """Pick a USD supply figure out of a DeFiLlama chainBalances entry."""
    if isinstance(balance, dict):
        for outer_key in ("current", "circulating"):
            outer = balance.get(outer_key)
            if isinstance(outer, dict):
                for inner_key in ("peggedUSD", "current", "circulating"):
                    value = as_float(outer.get(inner_key))
                    if value is not None:
                        return value
            else:
                value = as_float(outer)
                if value is not None:
                    return value
        for key in ("peggedUSD", "supply"):
            value = as_float(balance.get(key))
            if value is not None:
                return value
        return None
    return as_float(balance)


def is_stablecoin_history_point(point: Any) -> bool:
    """Return True when a /stablecoin/{id} history point can emit one row."""
    return (
        isinstance(point, dict)
        and parse_history_timestamp(point.get("date")) is not None
        and stablecoin_supply(point) is not None
    )
