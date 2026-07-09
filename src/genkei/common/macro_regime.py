"""Shared readers for macro-regime analytics outputs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from genkei.common import db


def load_macro_regime_labels(
    *,
    dates: Sequence[date] | None = None,
    since: date | None = None,
    until: date | None = None,
    ascending: bool = True,
) -> list[tuple[date, str]]:
    """Load ``(ts, regime)`` rows from ``analytics.macro_regime_per_date``.

    ``dates`` filters to exact dates. ``since`` / ``until`` bound a contiguous
    calendar window. Callers may combine exact dates with bounds, but the
    common uses today are either a date set or a bounded window.
    """
    where_clauses: list[str] = []
    params: list[Any] = []

    if dates is not None:
        unique_dates = sorted(set(dates))
        if not unique_dates:
            return []
        where_clauses.append("ts = ANY(%s)")
        params.append(unique_dates)
    if since is not None:
        where_clauses.append("ts >= %s")
        params.append(since)
    if until is not None:
        where_clauses.append("ts <= %s")
        params.append(until)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    order_sql = "ASC" if ascending else "DESC"
    sql = (
        "SELECT ts, regime FROM analytics.macro_regime_per_date "
        f"{where_sql} ORDER BY ts {order_sql}"
    )

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(ts, regime) for ts, regime in cur.fetchall()]
