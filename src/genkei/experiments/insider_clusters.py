"""Insider-transaction cluster detector (B-060).

Phase 5 experiment over the Form 4 data landed by B-079. A "cluster"
is N or more *distinct* reporting owners of the same issuer
transacting in the same direction within a K-day window. The signal
shape Buffett, Klarman, and Greenblatt all reference: when several
officers/directors of the same company buy on the open market within
a short window, they're acting on information that hasn't yet
reverted into the price.

Two roles in this module:

  * ``detect_clusters`` — pure algorithm. Takes pre-filtered
    ``Transaction`` records and returns ``Cluster`` records. No DB
    access, no CLI. Easy to test on synthetic data.
  * ``query_buy_candidates`` / ``query_sell_candidates`` — load the
    right transactions from ``sec.form4_transactions`` for a given
    direction + scope. Caller passes the result to ``detect_clusters``.

Direction semantics:

  * **buy** — ``transaction_code='P'`` (open-market purchase) AND
    ``acquired_disposed='A'`` AND ``is_derivative=false``. Excludes
    code ``A`` (grants/awards) which are compensation, not conviction.
  * **sell** — ``transaction_code='S'`` AND ``acquired_disposed='D'``
    AND ``is_derivative=false``. Excludes code ``F`` (tax-withholding
    sells) which are tax-driven, not conviction. Sell clusters are
    far more common than buy clusters and a weaker signal.

Window semantics: ``window_days`` is the maximum *span* (last date -
first date), not the gap between consecutive transactions. "5 officers
bought within a week" is materially stronger than "5 officers bought
over a year with no >7-day gap" — span captures the signal we want.

The detector is greedy: once a cluster is emitted starting at index
``i``, the scan advances past the cluster window before looking for
the next one. Overlapping clusters with the same reporters never
collapse to a single output.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from genkei.common import db

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Transaction:
    """One insider transaction in cluster-detector shape."""

    issuer_cik: str
    reporter_cik: str
    reporter_name: str
    transaction_date: date
    transaction_code: str
    acquired_disposed: str
    shares: Decimal
    price_usd: Decimal | None
    accession_number: str
    is_officer: bool = False
    is_director: bool = False
    is_ten_percent_owner: bool = False
    officer_title: str | None = None


@dataclass(frozen=True)
class ReporterSummary:
    """Per-reporter aggregate inside one cluster."""

    reporter_cik: str
    reporter_name: str
    shares: Decimal
    value_usd: Decimal | None
    is_officer: bool
    is_director: bool
    is_ten_percent_owner: bool
    officer_title: str | None


@dataclass(frozen=True)
class Cluster:
    """A detected insider-transaction cluster on one issuer."""

    issuer_cik: str
    direction: str  # 'buy' or 'sell'
    window_start: date
    window_end: date
    reporter_count: int
    total_shares: Decimal
    total_value_usd: Decimal | None
    reporters: list[ReporterSummary] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure detector
# ---------------------------------------------------------------------------


DEFAULT_MIN_REPORTERS = 2
DEFAULT_WINDOW_DAYS = 7


def detect_clusters(
    transactions: list[Transaction],
    *,
    direction: str,
    min_reporters: int = DEFAULT_MIN_REPORTERS,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[Cluster]:
    """Find clusters where >= ``min_reporters`` insiders transacted within ``window_days``.

    ``transactions`` should already be filtered to the desired
    direction (call ``query_buy_candidates`` or ``query_sell_candidates``
    if loading from the lake). The detector groups by issuer, sorts by
    date, and greedily emits the first qualifying window starting at
    each unconsumed position. ``direction`` is forwarded onto the
    output for downstream reporting.
    """
    if direction not in {"buy", "sell"}:
        raise ValueError(f"direction must be 'buy' or 'sell', got {direction!r}")
    if min_reporters < 2:
        raise ValueError("min_reporters must be >= 2 for a cluster to be meaningful")
    if window_days < 1:
        raise ValueError("window_days must be >= 1")

    by_issuer: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        by_issuer[t.issuer_cik].append(t)

    clusters: list[Cluster] = []
    for issuer_cik, txns in by_issuer.items():
        txns.sort(key=lambda t: (t.transaction_date, t.reporter_cik, t.accession_number))
        i = 0
        n = len(txns)
        while i < n:
            anchor_date = txns[i].transaction_date
            j = i
            while j < n and (txns[j].transaction_date - anchor_date).days <= window_days:
                j += 1
            window = txns[i:j]
            distinct = {t.reporter_cik for t in window}
            if len(distinct) >= min_reporters:
                clusters.append(_summarize(window, issuer_cik=issuer_cik, direction=direction))
                # Skip past the cluster so we don't emit overlapping ones.
                i = j
            else:
                i += 1
    # Sort: most reporters first, then most recent, then highest value
    clusters.sort(
        key=lambda c: (
            -c.reporter_count,
            -c.window_end.toordinal(),
            -(float(c.total_value_usd) if c.total_value_usd is not None else 0.0),
        )
    )
    return clusters


def _summarize(window: list[Transaction], *, issuer_cik: str, direction: str) -> Cluster:
    per_reporter: dict[str, list[Transaction]] = defaultdict(list)
    for t in window:
        per_reporter[t.reporter_cik].append(t)

    reporters: list[ReporterSummary] = []
    total_shares = Decimal(0)
    total_value = Decimal(0)
    has_any_value = False
    for cik, txns in per_reporter.items():
        rep_shares = sum((t.shares for t in txns), Decimal(0))
        rep_value: Decimal | None = None
        for t in txns:
            if t.price_usd is not None:
                rep_value = (rep_value or Decimal(0)) + (t.shares * t.price_usd)
                has_any_value = True
        total_shares += rep_shares
        if rep_value is not None:
            total_value += rep_value
        # Use the first transaction's reporter metadata; the dim is
        # the same across an insider's filings within a window.
        first = txns[0]
        reporters.append(
            ReporterSummary(
                reporter_cik=cik,
                reporter_name=first.reporter_name,
                shares=rep_shares,
                value_usd=rep_value,
                is_officer=first.is_officer,
                is_director=first.is_director,
                is_ten_percent_owner=first.is_ten_percent_owner,
                officer_title=first.officer_title,
            )
        )
    reporters.sort(key=lambda r: (-(r.value_usd or Decimal(0)), -r.shares, r.reporter_name))

    return Cluster(
        issuer_cik=issuer_cik,
        direction=direction,
        window_start=min(t.transaction_date for t in window),
        window_end=max(t.transaction_date for t in window),
        reporter_count=len({t.reporter_cik for t in window}),
        total_shares=total_shares,
        total_value_usd=total_value if has_any_value else None,
        reporters=reporters,
        transactions=list(window),
    )


# ---------------------------------------------------------------------------
# Lake-loading helpers
# ---------------------------------------------------------------------------


BUY_FILTER_SQL = (
    "transaction_code = 'P' AND acquired_disposed = 'A' AND is_derivative = false"
)
SELL_FILTER_SQL = (
    "transaction_code = 'S' AND acquired_disposed = 'D' AND is_derivative = false"
)


def query_buy_candidates(
    *,
    since: date | None = None,
    until: date | None = None,
    issuer_ciks: list[str] | None = None,
) -> list[Transaction]:
    return _query_candidates(
        BUY_FILTER_SQL, since=since, until=until, issuer_ciks=issuer_ciks
    )


def query_sell_candidates(
    *,
    since: date | None = None,
    until: date | None = None,
    issuer_ciks: list[str] | None = None,
) -> list[Transaction]:
    return _query_candidates(
        SELL_FILTER_SQL, since=since, until=until, issuer_ciks=issuer_ciks
    )


def _query_candidates(
    direction_filter: str,
    *,
    since: date | None,
    until: date | None,
    issuer_ciks: list[str] | None,
) -> list[Transaction]:
    sql = (
        "SELECT t.issuer_cik, t.reporter_cik, i.reporter_name, "
        "       t.transaction_date, t.transaction_code, t.acquired_disposed, "
        "       t.shares, t.price_usd, t.accession_number, "
        "       t.is_officer, t.is_director, t.is_ten_percent_owner, t.officer_title "
        "FROM sec.form4_transactions t "
        "JOIN sec.insiders i USING (reporter_cik) "
        f"WHERE {direction_filter} AND t.shares IS NOT NULL "
    )
    params: list[Any] = []
    if since is not None:
        sql += " AND t.transaction_date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND t.transaction_date <= %s"
        params.append(until)
    if issuer_ciks:
        sql += " AND t.issuer_cik = ANY(%s)"
        params.append(issuer_ciks)
    sql += " ORDER BY t.issuer_cik, t.transaction_date, t.reporter_cik"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        Transaction(
            issuer_cik=r[0],
            reporter_cik=r[1],
            reporter_name=r[2],
            transaction_date=r[3],
            transaction_code=r[4],
            acquired_disposed=r[5],
            shares=r[6],
            price_usd=r[7],
            accession_number=r[8],
            is_officer=bool(r[9]) if r[9] is not None else False,
            is_director=bool(r[10]) if r[10] is not None else False,
            is_ten_percent_owner=bool(r[11]) if r[11] is not None else False,
            officer_title=r[12],
        )
        for r in rows
    ]
