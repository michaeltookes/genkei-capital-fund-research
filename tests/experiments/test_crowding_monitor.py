"""Unit tests for the 13F crowding monitor (B-061)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from genkei.experiments.crowding_monitor import (
    CrowdingRow,
    Position,
    _prior_period,
    compute_crowding,
)


def _pos(
    *,
    filer_cik: str,
    filer_name: str = "Filer",
    period: date,
    cusip: str = "037833100",
    issuer_name: str | None = "APPLE INC",
    value_usd: Decimal | None = Decimal("1000"),
    shares: Decimal | None = Decimal("10"),
    accession_number: str | None = None,
) -> Position:
    """Tiny constructor for synthetic Positions in the detector tests."""
    return Position(
        filer_cik=filer_cik,
        filer_name=filer_name,
        period_of_report=period,
        cusip=cusip,
        issuer_name=issuer_name,
        value_usd=value_usd,
        shares_or_principal=shares,
        accession_number=accession_number or f"{filer_cik}-{period.isoformat()}",
    )


def _by_period(rows: list[CrowdingRow], cusip: str) -> dict[date, CrowdingRow]:
    return {r.period_of_report: r for r in rows if r.cusip == cusip}


class PriorPeriodHelperTests(unittest.TestCase):
    def test_returns_largest_period_strictly_before(self) -> None:
        periods = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30)]
        self.assertEqual(
            _prior_period(periods, date(2024, 9, 30)), date(2024, 6, 30)
        )

    def test_first_period_has_no_prior(self) -> None:
        periods = [date(2024, 3, 31), date(2024, 6, 30)]
        self.assertIsNone(_prior_period(periods, date(2024, 3, 31)))

    def test_handles_calendar_gaps(self) -> None:
        # Filer skipped a quarter — the prior period is still the
        # most-recent-earlier, even with a gap.
        periods = [date(2024, 3, 31), date(2024, 12, 31)]
        self.assertEqual(
            _prior_period(periods, date(2024, 12, 31)), date(2024, 3, 31)
        )


class SinglePeriodCrowdingTests(unittest.TestCase):
    def test_holder_count_and_aggregates(self) -> None:
        positions = [
            _pos(filer_cik="A", period=date(2025, 3, 31), value_usd=Decimal("100")),
            _pos(filer_cik="B", period=date(2025, 3, 31), value_usd=Decimal("200")),
        ]
        rows = compute_crowding(positions)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r.holder_count, 2)
        self.assertEqual(r.total_value_usd, Decimal("300"))
        self.assertEqual(r.total_shares, Decimal("20"))
        # First-observed period for this CUSIP has no delta state.
        self.assertIsNone(r.prior_holder_count)
        self.assertEqual(r.new_entrants, [])
        self.assertEqual(r.exits, [])
        self.assertIsNone(r.net_change)

    def test_holders_sorted_by_value_desc(self) -> None:
        positions = [
            _pos(
                filer_cik="SMALL",
                filer_name="Small Cap LLC",
                period=date(2025, 3, 31),
                value_usd=Decimal("100"),
            ),
            _pos(
                filer_cik="BIG",
                filer_name="Mega Cap LP",
                period=date(2025, 3, 31),
                value_usd=Decimal("9000"),
            ),
        ]
        rows = compute_crowding(positions)
        # The biggest holder by value lands first in the sorted list.
        self.assertEqual(rows[0].holder_names, ["Mega Cap LP", "Small Cap LLC"])
        self.assertEqual(rows[0].holder_ciks, ["BIG", "SMALL"])

    def test_null_value_still_counted_in_holder_count(self) -> None:
        # A filing landed with no value_usd (rare but possible —
        # malformed XML or NULL upstream). Should still bump holder
        # count by 1; dollar aggregates ignore the null.
        positions = [
            _pos(filer_cik="A", period=date(2025, 3, 31), value_usd=None),
            _pos(filer_cik="B", period=date(2025, 3, 31), value_usd=Decimal("500")),
        ]
        rows = compute_crowding(positions)
        self.assertEqual(rows[0].holder_count, 2)
        self.assertEqual(rows[0].total_value_usd, Decimal("500"))

    def test_dedupes_same_filer_across_accessions(self) -> None:
        # A filer with a 13F-HR and a follow-up 13F-HR/A for the same
        # period: count once, take the higher accession_number's value
        # (the amendment supersedes).
        positions = [
            _pos(
                filer_cik="A",
                period=date(2025, 3, 31),
                value_usd=Decimal("100"),
                accession_number="0001000000-25-000001",
            ),
            _pos(
                filer_cik="A",
                period=date(2025, 3, 31),
                value_usd=Decimal("250"),  # amended up
                accession_number="0001000000-25-000002",
            ),
        ]
        rows = compute_crowding(positions)
        self.assertEqual(rows[0].holder_count, 1)
        # Amendment's value wins, not the sum.
        self.assertEqual(rows[0].total_value_usd, Decimal("250"))


class DeltaCrowdingTests(unittest.TestCase):
    """Verify the prior-period delta logic — the actionable signal."""

    def _aapl_two_quarter_scenario(self) -> list[Position]:
        # Q4 2024: 2 holders (A, B). Q1 2025: 4 holders (A, C, D, E).
        # B exited; C, D, E are new entrants. Net change +2 (2 → 4).
        q4 = date(2024, 12, 31)
        q1 = date(2025, 3, 31)
        return [
            _pos(filer_cik="A", filer_name="A LP", period=q4),
            _pos(filer_cik="B", filer_name="B Capital", period=q4),
            _pos(filer_cik="A", filer_name="A LP", period=q1),
            _pos(filer_cik="C", filer_name="C Capital", period=q1),
            _pos(filer_cik="D", filer_name="D Partners", period=q1),
            _pos(filer_cik="E", filer_name="E Mgmt", period=q1),
        ]

    def test_new_entrants_and_exits_computed_correctly(self) -> None:
        rows = compute_crowding(self._aapl_two_quarter_scenario())
        by_period = _by_period(rows, "037833100")
        q1 = by_period[date(2025, 3, 31)]
        self.assertEqual(q1.holder_count, 4)
        self.assertEqual(q1.prior_holder_count, 2)
        self.assertEqual(q1.net_change, 2)
        # sorted ascending in the row contract
        self.assertEqual(q1.new_entrants, ["C", "D", "E"])
        self.assertEqual(q1.exits, ["B"])

    def test_first_period_for_a_cusip_has_no_delta(self) -> None:
        rows = compute_crowding(self._aapl_two_quarter_scenario())
        q4 = _by_period(rows, "037833100")[date(2024, 12, 31)]
        # Q4 is the first period observed for AAPL — no prior state.
        self.assertIsNone(q4.prior_holder_count)
        self.assertEqual(q4.new_entrants, [])
        self.assertEqual(q4.exits, [])
        self.assertIsNone(q4.net_change)

    def test_exit_only_quarter_surfaces_net_change_negative(self) -> None:
        # Last quarter 3 holders, this quarter only 1. Net change -2.
        q4 = date(2024, 12, 31)
        q1 = date(2025, 3, 31)
        positions = [
            _pos(filer_cik="A", period=q4),
            _pos(filer_cik="B", period=q4),
            _pos(filer_cik="C", period=q4),
            _pos(filer_cik="A", period=q1),
        ]
        rows = compute_crowding(positions)
        q1_row = _by_period(rows, "037833100")[q1]
        self.assertEqual(q1_row.holder_count, 1)
        self.assertEqual(q1_row.net_change, -2)
        self.assertEqual(sorted(q1_row.exits), ["B", "C"])
        self.assertEqual(q1_row.new_entrants, [])

    def test_delta_uses_positional_prior_not_calendar_quarter(self) -> None:
        # Filer set in 2024-Q1, then skipped 2024-Q2 and Q3, returned
        # in 2024-Q4. The delta should compare Q4 to Q1, not to an
        # imagined Q3 with 0 holders.
        positions = [
            _pos(filer_cik="A", period=date(2024, 3, 31)),
            _pos(filer_cik="B", period=date(2024, 3, 31)),
            _pos(filer_cik="A", period=date(2024, 12, 31)),
            _pos(filer_cik="B", period=date(2024, 12, 31)),
            _pos(filer_cik="C", period=date(2024, 12, 31)),
        ]
        rows = compute_crowding(positions)
        q4 = _by_period(rows, "037833100")[date(2024, 12, 31)]
        self.assertEqual(q4.prior_holder_count, 2)
        self.assertEqual(q4.net_change, 1)
        self.assertEqual(q4.new_entrants, ["C"])


class MultiCusipSortingTests(unittest.TestCase):
    def test_default_sort_latest_period_most_crowded_first(self) -> None:
        # Same period, two CUSIPs with different holder counts; plus an
        # earlier period for one of them.
        q1 = date(2025, 3, 31)
        q4 = date(2024, 12, 31)
        positions = [
            # AAPL Q1 has 3 holders
            _pos(filer_cik="A", period=q1, cusip="037833100", issuer_name="APPLE INC"),
            _pos(filer_cik="B", period=q1, cusip="037833100", issuer_name="APPLE INC"),
            _pos(filer_cik="C", period=q1, cusip="037833100", issuer_name="APPLE INC"),
            # MSFT Q1 has 2 holders
            _pos(filer_cik="A", period=q1, cusip="594918104", issuer_name="MICROSOFT"),
            _pos(filer_cik="B", period=q1, cusip="594918104", issuer_name="MICROSOFT"),
            # AAPL Q4 (earlier period) — should land last
            _pos(filer_cik="A", period=q4, cusip="037833100", issuer_name="APPLE INC"),
        ]
        rows = compute_crowding(positions)
        # Latest period first, and within that period most-crowded first.
        self.assertEqual(rows[0].period_of_report, q1)
        self.assertEqual(rows[0].cusip, "037833100")  # AAPL: 3 holders
        self.assertEqual(rows[1].period_of_report, q1)
        self.assertEqual(rows[1].cusip, "594918104")  # MSFT: 2 holders
        # AAPL Q4 (earlier) sorts last.
        self.assertEqual(rows[-1].period_of_report, q4)


class IssuerNameFallbackTests(unittest.TestCase):
    def test_first_non_null_issuer_name_caches(self) -> None:
        # If only one filer reported a non-null issuer_name, the
        # row should still surface that name on later periods where
        # all filers happen to have nulls.
        positions = [
            _pos(filer_cik="A", period=date(2024, 12, 31), issuer_name="APPLE INC"),
            _pos(filer_cik="A", period=date(2025, 3, 31), issuer_name=None),
            _pos(filer_cik="B", period=date(2025, 3, 31), issuer_name=None),
        ]
        rows = compute_crowding(positions)
        for r in rows:
            self.assertEqual(r.issuer_name, "APPLE INC")


if __name__ == "__main__":
    unittest.main()
