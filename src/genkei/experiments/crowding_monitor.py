"""13F crowding monitor (B-061).

Phase 5 experiment over the 13F holdings landed by B-080. *Crowding*
is the count of distinct watchlist filers holding the same security
(CUSIP) at the same quarter-end. The delta vs the prior quarter is
where the actionable signal lives: a name that just jumped from
1 → 4 watchlist managers in one quarter has multiple high-conviction
funds taking a position simultaneously — the institutional-positioning
analogue of B-060's insider buy-clusters.

Two roles in this module, mirroring the insider-cluster shape:

* ``compute_crowding`` — pure aggregator. Takes ``Position`` records,
  groups by ``(period_of_report, cusip)``, computes per-period
  holder counts + dollar exposure, and pairs each row with its
  prior-period state to derive ``new_entrants`` / ``exits`` /
  ``net_change``. No DB access, no CLI. Easy to test on synthetic data.

* ``load_positions`` — pulls the right rows from
  ``sec.form13f_holdings`` joined to ``sec.filers``. Caller passes
  the result to ``compute_crowding``.

Why this is rendered as a CLI module rather than a notebook (the
backlog's literal phrasing): per D-017 the project picked Claude Code
over notebooks as the agent harness, and every Phase 5 experiment so
far (B-058 / B-059 / B-060 / B-062 / B-065 / B-057 / B-090) has shipped
as `experiments/<name>.py` + `cli/<name>.py`. This one follows the
same convention.

Edge cases worth noting:

  * A CUSIP held by only one watchlist filer at a given period is
    "uncrowded" by the experiment's framing, but the *delta* signal
    can still be meaningful: 1 → 4 is exactly the activist-add
    pattern. The detector returns every (period, cusip) aggregate
    regardless of holder_count; ``min_holders`` is a presentation
    filter the CLI applies at render time.
  * Positions with ``value_usd = NULL`` are still counted in
    holder_count (the manager filed something, that's a signal),
    but contribute 0 to dollar-weighted aggregates.
  * 13F-NT (notice-only) filings carry no holdings — they're
    invisible to this experiment by design. Crowding measures actual
    reported positions.
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
class Position:
    """One 13F holding row in crowding-detector shape."""

    filer_cik: str
    filer_name: str
    period_of_report: date
    cusip: str
    issuer_name: str | None
    value_usd: Decimal | None
    shares_or_principal: Decimal | None
    accession_number: str


@dataclass(frozen=True)
class CrowdingRow:
    """Per (period, CUSIP) crowding aggregate plus delta-vs-prior."""

    period_of_report: date
    cusip: str
    issuer_name: str | None
    holder_count: int
    holder_ciks: list[str] = field(default_factory=list)
    holder_names: list[str] = field(default_factory=list)
    total_value_usd: Decimal | None = None
    total_shares: Decimal | None = None
    # Prior-period crowding state. None on the first-observed period for
    # this CUSIP (no comparison possible).
    prior_holder_count: int | None = None
    new_entrants: list[str] = field(default_factory=list)
    exits: list[str] = field(default_factory=list)
    net_change: int | None = None


# ---------------------------------------------------------------------------
# Pure detector
# ---------------------------------------------------------------------------


DEFAULT_MIN_HOLDERS = 2


def compute_crowding(positions: list[Position]) -> list[CrowdingRow]:
    """Aggregate ``Position`` rows into ``CrowdingRow``s with deltas.

    Returns *every* (period, cusip) aggregate regardless of holder count;
    callers filter via ``--min-holders`` at the CLI layer. Sorted by
    ``period_of_report DESC, holder_count DESC, cusip ASC`` so the
    default "latest period, most crowded first" rendering is correct.
    """
    # Bucket positions by (period, cusip). Within a bucket dedupe on
    # filer_cik — a single filer can in principle file two 13F-HRs for
    # the same period (e.g. a Q4 restatement), in which case we count
    # them once and take the most-recent accession's value.
    by_period_cusip: dict[tuple[date, str], dict[str, Position]] = defaultdict(dict)
    issuer_by_cusip: dict[str, str | None] = {}
    for p in positions:
        bucket = by_period_cusip[(p.period_of_report, p.cusip)]
        existing = bucket.get(p.filer_cik)
        # Later accession_number wins (string sort works — accession
        # numbers are timestamp-derived). Stable for restatement edges.
        if existing is None or p.accession_number > existing.accession_number:
            bucket[p.filer_cik] = p
        # Cache an issuer_name per CUSIP (first non-null wins) so we can
        # surface it on rows where this batch only has nulls.
        if p.issuer_name and issuer_by_cusip.get(p.cusip) is None:
            issuer_by_cusip[p.cusip] = p.issuer_name

    # For delta computation, we need the holder sets per period for
    # each CUSIP. Build a CUSIP → [(period, holder_ciks)] list sorted
    # ascending by period.
    periods_by_cusip: dict[str, list[date]] = defaultdict(list)
    holders_per_period: dict[tuple[date, str], set[str]] = {}
    for (period, cusip), filers in by_period_cusip.items():
        holders_per_period[(period, cusip)] = set(filers.keys())
        periods_by_cusip[cusip].append(period)
    for cusip in periods_by_cusip:
        periods_by_cusip[cusip].sort()

    rows: list[CrowdingRow] = []
    for (period, cusip), filers in by_period_cusip.items():
        current_holders = set(filers.keys())
        # Find the immediately prior period for this CUSIP. We compare
        # against whatever came *before*, not "the previous calendar
        # quarter" — gaps happen (a filer might dip below the $100M
        # threshold and stop filing 13F for a year) and using positional
        # prior is the robust framing.
        prior_period = _prior_period(periods_by_cusip[cusip], period)
        if prior_period is None:
            prior_count: int | None = None
            new_entrants: list[str] = []
            exits: list[str] = []
            net_change: int | None = None
        else:
            prior_holders = holders_per_period[(prior_period, cusip)]
            prior_count = len(prior_holders)
            new_entrants = sorted(current_holders - prior_holders)
            exits = sorted(prior_holders - current_holders)
            net_change = len(current_holders) - prior_count

        total_value: Decimal | None = None
        total_shares: Decimal | None = None
        for pos in filers.values():
            if pos.value_usd is not None:
                total_value = (total_value or Decimal(0)) + pos.value_usd
            if pos.shares_or_principal is not None:
                total_shares = (total_shares or Decimal(0)) + pos.shares_or_principal

        # Sort holder names by value desc so the human render is "biggest
        # holder first" without the caller re-sorting.
        sorted_filers = sorted(
            filers.values(),
            key=lambda pp: (
                -(pp.value_usd if pp.value_usd is not None else Decimal(0)),
                pp.filer_name,
            ),
        )
        rows.append(
            CrowdingRow(
                period_of_report=period,
                cusip=cusip,
                issuer_name=issuer_by_cusip.get(cusip),
                holder_count=len(current_holders),
                holder_ciks=[pp.filer_cik for pp in sorted_filers],
                holder_names=[pp.filer_name for pp in sorted_filers],
                total_value_usd=total_value,
                total_shares=total_shares,
                prior_holder_count=prior_count,
                new_entrants=new_entrants,
                exits=exits,
                net_change=net_change,
            )
        )

    rows.sort(
        key=lambda r: (
            -r.period_of_report.toordinal(),
            -r.holder_count,
            -(r.net_change if r.net_change is not None else 0),
            r.cusip,
        )
    )
    return rows


def _prior_period(periods_sorted_asc: list[date], current: date) -> date | None:
    """Return the largest period strictly before ``current``, or None."""
    prior: date | None = None
    for p in periods_sorted_asc:
        if p >= current:
            break
        prior = p
    return prior


# ---------------------------------------------------------------------------
# Lake loader
# ---------------------------------------------------------------------------


def load_positions(
    *,
    since: date | None = None,
    until: date | None = None,
    filer_ciks: list[str] | None = None,
    cusips: list[str] | None = None,
) -> list[Position]:
    """Load 13F positions from ``sec.form13f_holdings`` joined to filers.

    Optional scope filters:
      * ``since`` / ``until``  — bound the ``period_of_report``.
      * ``filer_ciks``         — limit to a subset of managers.
      * ``cusips``             — limit to specific securities.
    """
    if since is not None and until is not None and since > until:
        raise ValueError(f"since must be on or before until: {since} > {until}")

    sql = (
        "SELECT h.filer_cik, f.name AS filer_name, h.period_of_report, "
        "       h.cusip, h.issuer_name, h.value_usd, h.shares_or_principal, "
        "       h.accession_number "
        "FROM sec.form13f_holdings h "
        "JOIN sec.filers f ON f.filer_cik = h.filer_cik "
        "WHERE 1 = 1 "
    )
    params: list[Any] = []
    if since is not None:
        sql += " AND h.period_of_report >= %s"
        params.append(since)
    if until is not None:
        sql += " AND h.period_of_report <= %s"
        params.append(until)
    if filer_ciks:
        sql += " AND h.filer_cik = ANY(%s)"
        params.append(filer_ciks)
    if cusips:
        sql += " AND h.cusip = ANY(%s)"
        params.append([c.upper() for c in cusips])

    out: list[Position] = []
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for (
            filer_cik,
            filer_name,
            period,
            cusip,
            issuer_name,
            value_usd,
            shares,
            accession_number,
        ) in cur.fetchall():
            out.append(
                Position(
                    filer_cik=filer_cik,
                    filer_name=filer_name,
                    period_of_report=period,
                    cusip=cusip,
                    issuer_name=issuer_name,
                    value_usd=value_usd,
                    shares_or_principal=shares,
                    accession_number=accession_number,
                )
            )
    return out


def available_periods(*, filer_ciks: list[str] | None = None) -> list[date]:
    """Return the distinct ``period_of_report`` values present in the lake.

    Used by the CLI to pick the latest available period as a default
    when the user passes no ``--period`` / ``--since`` / ``--until``.
    """
    sql = "SELECT DISTINCT period_of_report FROM sec.form13f_holdings"
    params: list[Any] = []
    if filer_ciks:
        sql += " WHERE filer_cik = ANY(%s)"
        params.append(filer_ciks)
    sql += " ORDER BY period_of_report DESC"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [row[0] for row in cur.fetchall()]
