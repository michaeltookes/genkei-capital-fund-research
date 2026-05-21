"""Crypto peer relative-strength signal (B-090).

Phase 6 derived signal that joins `coingecko.market_data` against
itself across two assets to produce
``relative_strength_pct = asset_return_pct - peer_return_pct``
over a fixed set of trailing windows (default 7 / 30 / 90 / 180 /
365 days). Pairs naturally with B-062 ``revenue-divergence``:
divergence catches "price vs fundamentals" (intra-asset);
relative-strength catches "asset vs peer" (inter-asset).

Two roles in this module:

  * **Pure functions** (``compute_relative_strength``,
    ``compute_return_pct``) operate on plain dataclasses. No DB,
    no CLI. The same math the Postgres view runs server-side —
    duplicated in Python so the formula stays unit-testable and
    the view-vs-Python output can be cross-checked on synthetic
    data.
  * **Lake-loading helper** (``load_relative_strength``) queries
    the ``analytics.crypto_relative_strength`` view. The CLI
    composes the loader's output into human + JSON formats.

`lookback` semantics: the most recent price at-or-before
``latest_ts - window_days``. Each coin anchors on its own latest_ts
so late-arriving rows on one coin don't drift the others. If
either side has insufficient history (a newly-listed coin at the
365d window), the return is None and the relative_strength is too —
read this as "insufficient data" rather than zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from genkei.common import db

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PricePoint:
    """One day's price observation for a coin. Mirrors the lake row shape."""

    ts: date
    price_usd: Decimal


@dataclass(frozen=True)
class RelativeStrengthRow:
    """One row from ``analytics.crypto_relative_strength``."""

    asset: str
    peer: str
    horizon: str
    window_days: int
    asset_latest_ts: date | None
    asset_lookback_ts: date | None
    asset_latest_price: Decimal | None
    asset_lookback_price: Decimal | None
    asset_return_pct: Decimal | None
    peer_latest_ts: date | None
    peer_lookback_ts: date | None
    peer_latest_price: Decimal | None
    peer_lookback_price: Decimal | None
    peer_return_pct: Decimal | None
    relative_strength_pct: Decimal | None


DEFAULT_WINDOWS = (7, 30, 90, 180, 365)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def compute_return_pct(
    price_series: list[PricePoint],
    *,
    window_days: int,
) -> tuple[Decimal | None, PricePoint | None, PricePoint | None]:
    """Compute the trailing-window return for one price series.

    Returns ``(return_pct, latest_point, lookback_point)``. Any of
    the three may be None: ``latest`` is None when the series is
    empty, ``lookback`` is None when no observation is at-or-before
    the lookback target, and ``return_pct`` is None when either is
    None or the lookback price is zero.

    Anchors the lookback target on the series's own ``latest_ts`` —
    matching the Postgres view, so coin-specific freshness differences
    don't drift the comparison.
    """
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    if not price_series:
        return None, None, None
    sorted_series = sorted(price_series, key=lambda p: p.ts)
    latest = sorted_series[-1]
    target_ts = latest.ts - timedelta(days=window_days)
    lookback: PricePoint | None = None
    for point in reversed(sorted_series[:-1]):
        if point.ts <= target_ts:
            lookback = point
            break
    if lookback is None or lookback.price_usd == 0:
        return None, latest, lookback
    return_pct = (latest.price_usd - lookback.price_usd) / lookback.price_usd * Decimal(100)
    return return_pct, latest, lookback


def compute_relative_strength(
    asset_series: list[PricePoint],
    peer_series: list[PricePoint],
    *,
    asset: str,
    peer: str,
    horizon: str = "crypto:core",
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> list[RelativeStrengthRow]:
    """Compute relative-strength rows for an (asset, peer) pair across windows.

    Pure function: takes two sorted-or-unsorted price series and emits
    one ``RelativeStrengthRow`` per window. Equivalent to a single-pair
    slice of the ``analytics.crypto_relative_strength`` view, kept in
    Python for unit tests on synthetic data.
    """
    rows: list[RelativeStrengthRow] = []
    for w in windows:
        if w < 1:
            raise ValueError(f"window_days must be >= 1, got {w}")
        asset_return, asset_latest, asset_lookback = compute_return_pct(
            asset_series, window_days=w
        )
        peer_return, peer_latest, peer_lookback = compute_return_pct(
            peer_series, window_days=w
        )
        rel_strength = (
            asset_return - peer_return
            if asset_return is not None and peer_return is not None
            else None
        )
        rows.append(
            RelativeStrengthRow(
                asset=asset,
                peer=peer,
                horizon=horizon,
                window_days=w,
                asset_latest_ts=asset_latest.ts if asset_latest else None,
                asset_lookback_ts=asset_lookback.ts if asset_lookback else None,
                asset_latest_price=asset_latest.price_usd if asset_latest else None,
                asset_lookback_price=asset_lookback.price_usd if asset_lookback else None,
                asset_return_pct=asset_return,
                peer_latest_ts=peer_latest.ts if peer_latest else None,
                peer_lookback_ts=peer_lookback.ts if peer_lookback else None,
                peer_latest_price=peer_latest.price_usd if peer_latest else None,
                peer_lookback_price=peer_lookback.price_usd if peer_lookback else None,
                peer_return_pct=peer_return,
                relative_strength_pct=rel_strength,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Lake-loading helper
# ---------------------------------------------------------------------------


def load_relative_strength(
    *,
    asset: str | None = None,
    assets: Sequence[str] | None = None,
    peer: str | None = None,
    window_days: int | None = None,
    limit: int | None = None,
    asset_horizons: Mapping[str, str] | None = None,
) -> list[RelativeStrengthRow]:
    """Pull rows from ``analytics.crypto_relative_strength``.

    All filters are optional; any non-None value narrows the result.
    ``asset`` / ``assets`` / ``peer`` match exact coingecko_ids (case-sensitive).
    Rows are returned sorted by ``relative_strength_pct DESC NULLS
    LAST`` so the most-outperforming pairs surface first.
    """
    if asset is not None and assets is not None:
        raise ValueError("asset and assets filters are mutually exclusive")
    asset_list = list(dict.fromkeys(assets or []))
    if assets is not None and not asset_list:
        return []
    sql = (
        "SELECT asset, peer, window_days, "
        "asset_latest_ts, asset_lookback_ts, "
        "asset_latest_price, asset_lookback_price, asset_return_pct, "
        "peer_latest_ts, peer_lookback_ts, "
        "peer_latest_price, peer_lookback_price, peer_return_pct, "
        "relative_strength_pct "
        "FROM analytics.crypto_relative_strength "
        "WHERE 1=1"
    )
    params: list[Any] = []
    if asset is not None:
        sql += " AND asset = %s"
        params.append(asset)
    if assets is not None:
        sql += " AND asset = ANY(%s)"
        params.append(asset_list)
    if peer is not None:
        sql += " AND peer = %s"
        params.append(peer)
    if window_days is not None:
        sql += " AND window_days = %s"
        params.append(window_days)
    sql += " ORDER BY relative_strength_pct DESC NULLS LAST, asset, peer, window_days"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        RelativeStrengthRow(
            asset=r[0],
            peer=r[1],
            horizon=(asset_horizons or {}).get(r[0], "crypto:unknown"),
            window_days=int(r[2]),
            asset_latest_ts=_to_date(r[3]),
            asset_lookback_ts=_to_date(r[4]),
            asset_latest_price=r[5],
            asset_lookback_price=r[6],
            asset_return_pct=r[7],
            peer_latest_ts=_to_date(r[8]),
            peer_lookback_ts=_to_date(r[9]),
            peer_latest_price=r[10],
            peer_lookback_price=r[11],
            peer_return_pct=r[12],
            relative_strength_pct=r[13],
        )
        for r in rows
    ]


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date()
    return value
