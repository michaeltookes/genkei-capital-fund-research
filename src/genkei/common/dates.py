"""Shared date helpers for ingesters.

Backfills that walk a long history in bounded API windows all need the same
primitive: split an inclusive ``[start, end]`` span into consecutive windows
of at most ``chunk_days`` days. The Coinbase candles backfill (300-row cap)
and the CoinGecko ``/market_chart/range`` backfill each grew their own copy;
this is the single source of truth they now share (B-121).
"""

from __future__ import annotations

from datetime import date, timedelta


def iter_date_windows(
    start: date, end: date, *, chunk_days: int
) -> list[tuple[date, date]]:
    """Split the inclusive ``[start, end]`` span into consecutive windows.

    Each window is an inclusive ``(window_start, window_end)`` date pair of at
    most ``chunk_days`` days; the final window is shorter when the span doesn't
    divide evenly. ``window_end`` is the *last day included* in the chunk — the
    per-source URL builders convert to half-open UTC datetimes downstream.

    Raises ``ValueError`` if ``chunk_days <= 0`` or ``end < start`` (a backwards
    span is a caller bug, not an empty result).
    """
    if chunk_days <= 0:
        raise ValueError(f"chunk_days must be > 0, got {chunk_days}")
    if end < start:
        raise ValueError(f"end {end} < start {start}")
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=chunk_days - 1), end)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows
