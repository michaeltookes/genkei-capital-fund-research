"""Unit tests for the SEC 10-Q/10-K ETF shares-outstanding extractor (B-114)."""

from __future__ import annotations

import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from genkei.common.watchlist import EtfTickerEntry
from genkei.ingest import sec_etf_shares
from genkei.ingest.sec_etf_shares import (
    SOURCE_ENDPOINT_MARKER,
    build_snapshots,
    extract_checkpoints,
)


def _fact(end: str, val: float, *, form: str = "10-Q", filed: str = "2024-05-08") -> dict:
    return {"end": end, "val": val, "form": form, "filed": filed}


def _facts(*, shares: list[dict], net_assets: list[dict]) -> dict:
    return {
        "us-gaap": {
            "TemporaryEquitySharesOutstanding": {"units": {"shares": shares}},
            "FairValueNetAssetLiability": {"units": {"USD": net_assets}},
        }
    }


def _entry(*, launch: str | None = "2024-01-11") -> EtfTickerEntry:
    return EtfTickerEntry(
        ticker="IBIT",
        name="iShares Bitcoin Trust ETF",
        asset="BTC",
        issuer="BlackRock",
        launch_date=launch,
        cik="0001980994",
    )


def _write_watchlist(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    path = Path(ctx.name) / "watchlists.yml"
    path.write_text(
        "\n".join(
            [
                "etf_tickers:",
                "  - ticker: IBIT",
                "    name: iShares Bitcoin Trust ETF",
                "    asset: BTC",
                "    issuer: BlackRock",
                "    launch_date: 2024-01-11",
                "    cik: '0001980994'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class _FakeRun:
    id = 42

    def __init__(self) -> None:
        self.rows_written = 0

    def add_rows(self, n: int) -> None:
        self.rows_written += n


class _FakeHttp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def get_json(self, url: str) -> dict[str, Any]:
        return self.payload


@contextmanager
def _fake_ingest_run(*args: object, **kwargs: object) -> Iterator[_FakeRun]:
    yield _FakeRun()


class ExtractCheckpointsTests(unittest.TestCase):
    def test_earliest_filed_wins_for_duplicate_end(self) -> None:
        # 2024-12-31 first reported by the 10-K (val 970), then repeated as a
        # prior-period comparative in a later 10-Q (val 999 — a decoy). The
        # original filing must win.
        facts = _facts(
            shares=[
                _fact("2024-12-31", 970, form="10-K", filed="2025-03-05"),
                _fact("2024-12-31", 999, form="10-Q", filed="2025-05-07"),
            ],
            net_assets=[],
        )
        cps = extract_checkpoints(facts, sec_etf_shares.SHARE_CONCEPTS)
        self.assertEqual(cps[date(2024, 12, 31)], Decimal("970"))

    def test_non_period_forms_ignored(self) -> None:
        facts = _facts(
            shares=[
                _fact("2024-03-31", 442, form="8-K", filed="2024-04-01"),
                _fact("2024-06-30", 539, form="10-Q", filed="2024-08-08"),
            ],
            net_assets=[],
        )
        cps = extract_checkpoints(facts, sec_etf_shares.SHARE_CONCEPTS)
        self.assertNotIn(date(2024, 3, 31), cps)
        self.assertIn(date(2024, 6, 30), cps)

    def test_absent_concept_returns_empty(self) -> None:
        cps = extract_checkpoints({"us-gaap": {}}, sec_etf_shares.SHARE_CONCEPTS)
        self.assertEqual(cps, {})

    def test_first_nonempty_concept_wins(self) -> None:
        # A two-candidate list where the first is absent falls through to the
        # second.
        facts = {
            "us-gaap": {
                "SharesOutstanding": {"units": {"shares": [_fact("2024-06-30", 10)]}}
            }
        }
        concepts = (
            ("us-gaap", "TemporaryEquitySharesOutstanding", "shares"),
            ("us-gaap", "SharesOutstanding", "shares"),
        )
        cps = extract_checkpoints(facts, concepts)
        self.assertEqual(cps[date(2024, 6, 30)], Decimal("10"))


class BuildSnapshotsTests(unittest.TestCase):
    def test_joins_and_derives_nav(self) -> None:
        facts = _facts(
            shares=[_fact("2024-03-31", 442400000)],
            net_assets=[_fact("2024-03-31", 17788884882)],
        )
        snaps = build_snapshots({"facts": facts}, entry=_entry())
        self.assertEqual(len(snaps), 1)
        s = snaps[0]
        self.assertEqual(s.snapshot_date, date(2024, 3, 31))
        self.assertEqual(s.shares_outstanding, Decimal("442400000.0000"))
        self.assertEqual(s.total_net_assets_usd, Decimal("17788884882.00"))
        # NAV = TNA / shares
        self.assertEqual(s.nav_per_share_usd, Decimal("40.20995679"))
        self.assertEqual(s.ticker, "IBIT")
        self.assertEqual(s.asset, "BTC")

    def test_period_end_needs_both_facts(self) -> None:
        # Shares at 2024-06-30 but net-assets only at 2024-03-31 → no join.
        facts = _facts(
            shares=[_fact("2024-06-30", 539160000)],
            net_assets=[_fact("2024-03-31", 17788884882)],
        )
        self.assertEqual(build_snapshots({"facts": facts}, entry=_entry()), [])

    def test_pre_launch_rows_dropped(self) -> None:
        facts = _facts(
            shares=[
                _fact("2023-12-31", 4000, form="10-K", filed="2024-03-01"),
                _fact("2024-03-31", 442400000),
            ],
            net_assets=[
                _fact("2023-12-31", 100000, form="10-K", filed="2024-03-01"),
                _fact("2024-03-31", 17788884882),
            ],
        )
        snaps = build_snapshots({"facts": facts}, entry=_entry(launch="2024-01-11"))
        self.assertEqual([s.snapshot_date for s in snaps], [date(2024, 3, 31)])

    def test_zero_shares_dropped(self) -> None:
        facts = _facts(
            shares=[_fact("2024-03-31", 0)],
            net_assets=[_fact("2024-03-31", 100)],
        )
        self.assertEqual(build_snapshots({"facts": facts}, entry=_entry()), [])

    def test_no_share_concept_returns_empty(self) -> None:
        facts = {"us-gaap": {"FairValueNetAssetLiability": {"units": {"USD": []}}}}
        self.assertEqual(build_snapshots({"facts": facts}, entry=_entry()), [])

    def test_missing_facts_object_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "no 'facts' object"):
            build_snapshots({}, entry=_entry())


class ModuleConstantsTests(unittest.TestCase):
    def test_source_endpoint_marker_is_stable(self) -> None:
        # The net-flow query in etf_flows keys its exclusion on this exact
        # string — a rename here must be a deliberate coordinated change.
        self.assertEqual(SOURCE_ENDPOINT_MARKER, "sec_10q_xbrl")

    def test_source_name(self) -> None:
        self.assertEqual(sec_etf_shares.SOURCE_NAME, "sec_etf_shares")


class CollectTests(unittest.TestCase):
    def test_successful_payload_with_zero_checkpoints_fails_the_run(self) -> None:
        path = _write_watchlist(self)
        http = _FakeHttp({"facts": {"us-gaap": {}}})

        with (
            patch("genkei.ingest.sec_etf_shares.db.ingest_run", _fake_ingest_run),
            patch("genkei.ingest.sec_etf_shares.db.store_raw_blob"),
            patch("genkei.ingest.sec_etf_shares.db.record_partial_endpoints") as partial,
            self.assertRaisesRegex(RuntimeError, "no SEC quarter-end checkpoints parsed"),
        ):
            sec_etf_shares.collect(path, http=http)

        partial.assert_not_called()


if __name__ == "__main__":
    unittest.main()
