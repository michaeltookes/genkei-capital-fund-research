"""Unit tests for the insider-cluster detector (B-060).

Pure-algorithm tests on synthetic ``Transaction`` records. No DB,
no network — the SQL-loading helpers
(``query_buy_candidates`` / ``query_sell_candidates``) are tested
indirectly via the CLI command tests (which mock them out).
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from genkei.experiments.insider_clusters import (
    DEFAULT_MIN_REPORTERS,
    DEFAULT_WINDOW_DAYS,
    Transaction,
    _query_candidates,
    detect_clusters,
)


def _tx(
    *,
    reporter: str,
    day: int,
    issuer: str = "0000320193",
    shares: int = 100,
    price: float | None = 200.0,
    direction: str = "buy",  # 'buy' or 'sell'
    accession: str | None = None,
) -> Transaction:
    """Build a single Transaction with sensible defaults for testing."""
    if direction == "buy":
        code, acq = "P", "A"
    else:
        code, acq = "S", "D"
    return Transaction(
        issuer_cik=issuer,
        reporter_cik=reporter,
        reporter_name=f"Reporter {reporter[-2:]}",
        transaction_date=date(2026, 5, day),
        transaction_code=code,
        acquired_disposed=acq,
        shares=Decimal(shares),
        price_usd=Decimal(str(price)) if price is not None else None,
        accession_number=accession or f"acc-{reporter}-{day}",
        is_officer=True,
        officer_title="VP",
    )


class GuardsTests(unittest.TestCase):
    def test_rejects_invalid_direction(self) -> None:
        with self.assertRaises(ValueError):
            detect_clusters([], direction="hold")

    def test_rejects_min_reporters_below_2(self) -> None:
        with self.assertRaises(ValueError):
            detect_clusters([], direction="buy", min_reporters=1)

    def test_rejects_window_days_below_1(self) -> None:
        with self.assertRaises(ValueError):
            detect_clusters([], direction="buy", window_days=0)

    def test_rejects_unapproved_sql_direction_filter(self) -> None:
        with self.assertRaises(ValueError):
            _query_candidates(
                "1 = 1",
                since=None,
                until=None,
                issuer_ciks=None,
            )


class CoreDetectionTests(unittest.TestCase):
    def test_no_transactions_returns_empty(self) -> None:
        self.assertEqual(detect_clusters([], direction="buy"), [])

    def test_single_reporter_never_clusters(self) -> None:
        # One insider buying lots doesn't make a cluster.
        txns = [_tx(reporter="0000000001", day=d) for d in (1, 2, 3, 4, 5)]
        self.assertEqual(detect_clusters(txns, direction="buy"), [])

    def test_n_minus_one_reporters_does_not_cluster(self) -> None:
        # min_reporters defaults to 2; one reporter buying twice does not qualify.
        txns = [
            _tx(reporter="0000000001", day=1),
            _tx(reporter="0000000001", day=3),
        ]
        self.assertEqual(detect_clusters(txns, direction="buy"), [])

    def test_two_reporters_within_window_form_cluster(self) -> None:
        txns = [
            _tx(reporter="0000000001", day=1, shares=100, price=200),
            _tx(reporter="0000000002", day=3, shares=200, price=210),
        ]
        clusters = detect_clusters(txns, direction="buy")
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertEqual(c.reporter_count, 2)
        self.assertEqual(c.window_start, date(2026, 5, 1))
        self.assertEqual(c.window_end, date(2026, 5, 3))
        self.assertEqual(c.total_shares, Decimal(300))
        # 100*200 + 200*210 = 20_000 + 42_000 = 62_000
        self.assertEqual(c.total_value_usd, Decimal(62_000))
        self.assertEqual(c.direction, "buy")
        self.assertEqual(len(c.reporters), 2)
        self.assertEqual(len(c.transactions), 2)

    def test_reporters_outside_window_do_not_cluster(self) -> None:
        # 8 days apart with default window_days=7 → no cluster.
        txns = [
            _tx(reporter="0000000001", day=1),
            _tx(reporter="0000000002", day=9),
        ]
        self.assertEqual(detect_clusters(txns, direction="buy"), [])

    def test_window_boundary_inclusive(self) -> None:
        # Exactly window_days apart should cluster (span == window).
        txns = [
            _tx(reporter="0000000001", day=1),
            _tx(reporter="0000000002", day=1 + DEFAULT_WINDOW_DAYS),
        ]
        self.assertEqual(len(detect_clusters(txns, direction="buy")), 1)


class IssuerIsolationTests(unittest.TestCase):
    def test_does_not_merge_across_issuers(self) -> None:
        # Two reporters but each on a different issuer — no cluster.
        txns = [
            _tx(reporter="0000000001", day=1, issuer="ISS_A"),
            _tx(reporter="0000000002", day=2, issuer="ISS_B"),
        ]
        self.assertEqual(detect_clusters(txns, direction="buy"), [])

    def test_emits_one_cluster_per_qualifying_issuer(self) -> None:
        txns = [
            _tx(reporter="A1", day=1, issuer="ISS_A"),
            _tx(reporter="A2", day=2, issuer="ISS_A"),
            _tx(reporter="B1", day=3, issuer="ISS_B"),
            _tx(reporter="B2", day=4, issuer="ISS_B"),
        ]
        clusters = detect_clusters(txns, direction="buy")
        self.assertEqual({c.issuer_cik for c in clusters}, {"ISS_A", "ISS_B"})


class GreedyAdvanceTests(unittest.TestCase):
    def test_advances_past_emitted_cluster(self) -> None:
        # Five transactions spanning days 1..12, window=7.
        # Anchor at day 1: window extends to day 8 (within 7d of day 1),
        # capturing R1+R2+R3 → 3-reporter cluster spanning 1..8.
        # Greedy advance past the window, anchor at day 10:
        # window includes day 12 (within 7d), R4+R5 → 2-reporter
        # cluster spanning 10..12.
        # The point of this test: the second cluster does NOT
        # overlap the first one — the greedy advance prevents
        # double-counting transactions across clusters.
        txns = [
            _tx(reporter="R1", day=1),
            _tx(reporter="R2", day=3),
            _tx(reporter="R3", day=8),
            _tx(reporter="R4", day=10),
            _tx(reporter="R5", day=12),
        ]
        clusters = detect_clusters(txns, direction="buy")
        self.assertEqual(len(clusters), 2)
        date_pairs = {(c.window_start, c.window_end) for c in clusters}
        self.assertIn((date(2026, 5, 1), date(2026, 5, 8)), date_pairs)
        self.assertIn((date(2026, 5, 10), date(2026, 5, 12)), date_pairs)


class ConfigOverrideTests(unittest.TestCase):
    def test_higher_min_reporters_suppresses_two_reporter_window(self) -> None:
        txns = [
            _tx(reporter="R1", day=1),
            _tx(reporter="R2", day=2),
        ]
        self.assertEqual(
            detect_clusters(txns, direction="buy", min_reporters=3), []
        )

    def test_three_reporters_pass_min_reporters_3(self) -> None:
        txns = [
            _tx(reporter="R1", day=1),
            _tx(reporter="R2", day=2),
            _tx(reporter="R3", day=3),
        ]
        clusters = detect_clusters(txns, direction="buy", min_reporters=3)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].reporter_count, 3)

    def test_wider_window_groups_previously_split_clusters(self) -> None:
        # 1, 5, 9 with window=7 normally: {1,5}, then 9 alone. With
        # window=14 they're all one cluster spanning 1..9.
        txns = [
            _tx(reporter="R1", day=1),
            _tx(reporter="R2", day=5),
            _tx(reporter="R3", day=9),
        ]
        narrow = detect_clusters(txns, direction="buy", window_days=7)
        wide = detect_clusters(txns, direction="buy", window_days=14)
        self.assertEqual(len(narrow), 1)  # {R1, R2}
        self.assertEqual(narrow[0].reporter_count, 2)
        self.assertEqual(len(wide), 1)  # {R1, R2, R3}
        self.assertEqual(wide[0].reporter_count, 3)


class OutputShapeTests(unittest.TestCase):
    def test_reporters_summarize_per_reporter_value(self) -> None:
        txns = [
            _tx(reporter="R1", day=1, shares=100, price=200),
            _tx(reporter="R1", day=2, shares=50, price=210),  # same reporter, 2 txns
            _tx(reporter="R2", day=2, shares=300, price=205),
        ]
        clusters = detect_clusters(txns, direction="buy")
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertEqual(c.reporter_count, 2)  # R1 + R2, not 3
        per_cik = {r.reporter_cik: r for r in c.reporters}
        self.assertEqual(per_cik["R1"].shares, Decimal(150))
        # R1: 100*200 + 50*210 = 30_500
        self.assertEqual(per_cik["R1"].value_usd, Decimal(30_500))
        # R2: 300*205 = 61_500
        self.assertEqual(per_cik["R2"].value_usd, Decimal(61_500))

    def test_value_total_is_none_when_no_price_data(self) -> None:
        txns = [
            _tx(reporter="R1", day=1, price=None),
            _tx(reporter="R2", day=2, price=None),
        ]
        clusters = detect_clusters(txns, direction="buy")
        self.assertEqual(len(clusters), 1)
        self.assertIsNone(clusters[0].total_value_usd)

    def test_clusters_sorted_by_reporter_count_then_recency(self) -> None:
        # Two issuers; one has 3 reporters older, one has 2 reporters newer.
        # Sort order: 3-reporter first (size beats recency).
        txns = [
            _tx(reporter="A1", day=1, issuer="OLD"),
            _tx(reporter="A2", day=2, issuer="OLD"),
            _tx(reporter="A3", day=3, issuer="OLD"),
            _tx(reporter="B1", day=10, issuer="NEW"),
            _tx(reporter="B2", day=11, issuer="NEW"),
        ]
        clusters = detect_clusters(txns, direction="buy")
        self.assertEqual(clusters[0].issuer_cik, "OLD")
        self.assertEqual(clusters[0].reporter_count, 3)
        self.assertEqual(clusters[1].issuer_cik, "NEW")
        self.assertEqual(clusters[1].reporter_count, 2)

    def test_cluster_sort_ties_break_by_issuer_then_window_start(self) -> None:
        txns = [
            _tx(reporter="A1", day=1, issuer="B"),
            _tx(reporter="A2", day=2, issuer="B"),
            _tx(reporter="B1", day=1, issuer="A"),
            _tx(reporter="B2", day=2, issuer="A"),
        ]
        clusters = detect_clusters(txns, direction="buy")
        self.assertEqual([c.issuer_cik for c in clusters], ["A", "B"])


class DefaultsAreSensibleTests(unittest.TestCase):
    def test_defaults_match_documented_thresholds(self) -> None:
        # The docstring promises buys-by-2+-within-7-days. Pin both.
        self.assertEqual(DEFAULT_MIN_REPORTERS, 2)
        self.assertEqual(DEFAULT_WINDOW_DAYS, 7)


if __name__ == "__main__":
    unittest.main()
