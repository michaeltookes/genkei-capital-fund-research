"""Unit tests for the `genkei top-ranks` subcommand (B-149). Offline: db is mocked."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from typer.testing import CliRunner

from genkei.cli import app, main
from genkei.cli.top_ranks import build_diff, build_snapshot
from genkei.common.watchlist import Watchlist

D_A = date(2026, 10, 29)
D_B = date(2027, 1, 8)
STABLES = frozenset({"tether", "usd-coin"})


def _row(cgid: str, symbol: str, rank: int, mcap: float = 1e9) -> dict:
    return {
        "coingecko_id": cgid,
        "symbol": symbol,
        "rank": rank,
        "market_cap_usd": mcap,
        "price_usd": 1.5,
        "volume_usd": 1e6,
        "fully_diluted_usd": None,
    }


# Snapshot A: BTC, ETH, USDT (stable), XRP, BNB, SOL
SNAP_A = [
    _row("bitcoin", "BTC", 1, 2e12),
    _row("ethereum", "ETH", 2, 4e11),
    _row("tether", "USDT", 3, 1.8e11),
    _row("ripple", "XRP", 4, 1.5e11),
    _row("binancecoin", "BNB", 5, 1.4e11),
    _row("solana", "SOL", 6, 1.0e11),
]
# Snapshot B: SOL overtakes BNB and XRP, DOGE enters at 6, BNB falls out of the top 6
SNAP_B = [
    _row("bitcoin", "BTC", 1, 2.1e12),
    _row("ethereum", "ETH", 2, 4e11),
    _row("tether", "USDT", 3, 1.9e11),
    _row("solana", "SOL", 4, 1.6e11),
    _row("ripple", "XRP", 5, 1.5e11),
    _row("dogecoin", "DOGE", 6, 0.9e11),
    _row("binancecoin", "BNB", 7, 0.8e11),
]


def _watchlist() -> Watchlist:
    return Watchlist(
        crypto=[], equities=[], macro=[], protocols=[], filers=[], stablecoin_ids=tuple(STABLES)
    )


class BuildSnapshotTests(unittest.TestCase):
    def test_top_n_keeps_raw_rank_and_flags_stables(self) -> None:
        snap = build_snapshot(SNAP_A, ts=D_A, n=4, stable_ids=STABLES, exclude_stables=False)
        self.assertEqual([r["symbol"] for r in snap["rows"]], ["BTC", "ETH", "USDT", "XRP"])
        self.assertEqual([r["adj_rank"] for r in snap["rows"]], [1, 2, 3, 4])
        self.assertTrue(snap["rows"][2]["is_stable"])

    def test_exclude_stables_reranks_the_remainder(self) -> None:
        snap = build_snapshot(SNAP_A, ts=D_A, n=4, stable_ids=STABLES, exclude_stables=True)
        self.assertEqual([r["symbol"] for r in snap["rows"]], ["BTC", "ETH", "XRP", "BNB"])
        self.assertEqual([r["adj_rank"] for r in snap["rows"]], [1, 2, 3, 4])
        self.assertEqual([r["rank"] for r in snap["rows"]], [1, 2, 4, 5])


class BuildDiffTests(unittest.TestCase):
    def _diff(self, **kw) -> dict:
        args = dict(date_a=D_A, date_b=D_B, n=5, stable_ids=STABLES, exclude_stables=False)
        args.update(kw)
        return build_diff(SNAP_A, SNAP_B, **args)

    def test_entries_exits_and_moves(self) -> None:
        diff = self._diff()
        # SOL was #6 in A, is #4 in B: enters the top 5 and carries its old rank
        self.assertEqual([e["symbol"] for e in diff["entries"]], ["SOL"])
        self.assertEqual((diff["entries"][0]["rank_a"], diff["entries"][0]["rank_b"]), (6, 4))
        # BNB was #5, is now #7: exits the top 5 but is still in the stored top 50
        self.assertEqual([e["symbol"] for e in diff["exits"]], ["BNB"])
        self.assertEqual((diff["exits"][0]["rank_a"], diff["exits"][0]["rank_b"]), (5, 7))
        # XRP slipped one place inside the top 5
        self.assertEqual([e["symbol"] for e in diff["moves"]], ["XRP"])
        self.assertEqual(diff["moves"][0]["change"], -1)
        self.assertEqual(diff["unchanged"], 3)
        self.assertEqual(diff["missing_dates"], [])

    def test_entry_absent_from_the_earlier_snapshot_has_no_prior_rank(self) -> None:
        dg = {e["symbol"]: e for e in self._diff(n=6)["entries"]}
        self.assertIsNone(dg["DOGE"]["rank_a"])
        self.assertEqual(dg["DOGE"]["rank_b"], 6)

    def test_exclude_stables_changes_membership(self) -> None:
        diff = self._diff(n=3, exclude_stables=True)
        # ex-stables A: BTC ETH XRP ; B: BTC ETH SOL
        self.assertEqual([e["symbol"] for e in diff["entries"]], ["SOL"])
        self.assertEqual([e["symbol"] for e in diff["exits"]], ["XRP"])

    def test_missing_snapshot_does_not_report_membership_changes(self) -> None:
        for rows_a, rows_b, missing in (
            ([], SNAP_B, [D_A]), (SNAP_A, [], [D_B]), ([], [], [D_A, D_B])
        ):
            for exclude_stables in (False, True):
                with self.subTest(missing=missing, exclude_stables=exclude_stables):
                    diff = build_diff(
                        rows_a, rows_b, date_a=D_A, date_b=D_B, n=5,
                        stable_ids=STABLES, exclude_stables=exclude_stables,
                    )
                    self.assertEqual(diff["missing_dates"], missing)
                    self.assertEqual(diff["entries"], [])
                    self.assertEqual(diff["exits"], [])
                    self.assertEqual(diff["moves"], [])
                    self.assertEqual(diff["unchanged"], 0)

    def test_snapshot_empty_after_filtering_is_not_missing(self) -> None:
        stables_only = [_row("tether", "USDT", 1)]
        for rows_a, rows_b, change, missing_change in (
            (stables_only, SNAP_B, "entries", "exits"),
            (SNAP_A, stables_only, "exits", "entries"),
        ):
            with self.subTest(change=change):
                diff = build_diff(
                    rows_a, rows_b, date_a=D_A, date_b=D_B, n=3,
                    stable_ids=STABLES, exclude_stables=True,
                )
                self.assertEqual(diff["missing_dates"], [])
                self.assertEqual(len(diff[change]), 3)
                self.assertEqual(diff[missing_change], [])


class TopRanksCommandTests(unittest.TestCase):
    def _run(self, args: list[str], snaps: dict, latest: date | None = D_A) -> str:
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.top_ranks._fetch_snapshots",
                side_effect=lambda ds: {d: snaps.get(d, []) for d in ds},
            ),
            patch("genkei.cli.top_ranks._latest_date", return_value=latest),
            patch("genkei.cli.top_ranks.load_watchlist", return_value=_watchlist()),
            redirect_stdout(out),
        ):
            main(["top-ranks", *args])
        return out.getvalue()

    def test_default_uses_latest_snapshot(self) -> None:
        text = self._run(["--n", "3"], {D_A: SNAP_A})
        self.assertIn("2026-10-29", text)
        self.assertIn("BTC", text)
        self.assertIn("stable", text)
        self.assertNotIn("XRP", text)

    def test_json_exclude_stables(self) -> None:
        payload = json_mod.loads(
            self._run(["--n", "3", "--exclude-stables", "--json"], {D_A: SNAP_A})
        )
        self.assertEqual(payload["date"], "2026-10-29")
        self.assertEqual([r["symbol"] for r in payload["rows"]], ["BTC", "ETH", "XRP"])

    def test_explicit_date_without_snapshot_explains_forward_only(self) -> None:
        text = self._run(["--date", "2026-01-01"], {D_A: SNAP_A})
        self.assertIn("forward-only", text)

    def test_empty_table_has_no_latest_date(self) -> None:
        text = self._run([], {}, latest=None)
        self.assertIn("forward-only", text)

    def test_diff_human_and_json(self) -> None:
        snaps = {D_A: SNAP_A, D_B: SNAP_B}
        text = self._run(["--diff", "2026-10-29", "2027-01-08", "--n", "5"], snaps)
        self.assertIn("Entered", text)
        self.assertIn("Exited", text)
        self.assertIn("BNB", text)
        payload = json_mod.loads(
            self._run(["--diff", "2026-10-29", "2027-01-08", "--n", "5", "--json"], snaps)
        )
        self.assertEqual(payload["date_b"], "2027-01-08")
        self.assertEqual([e["symbol"] for e in payload["exits"]], ["BNB"])

    def test_diff_from_to_aliases_match_diff(self) -> None:
        snaps = {D_A: SNAP_A, D_B: SNAP_B}
        a = self._run(["--diff", "2026-10-29", "2027-01-08", "--json"], snaps)
        b = self._run(["--diff-from", "2026-10-29", "--diff-to", "2027-01-08", "--json"], snaps)
        self.assertEqual(a, b)

    def test_diff_with_missing_snapshot_says_so(self) -> None:
        text = self._run(["--diff", "2026-10-29", "2027-01-08"], {D_A: SNAP_A})
        self.assertIn("No snapshot for: 2027-01-08", text)

    def test_diff_json_with_missing_snapshot_has_no_comparison(self) -> None:
        for snaps, missing in (
            ({D_B: SNAP_B}, [D_A]), ({D_A: SNAP_A}, [D_B]), ({}, [D_A, D_B])
        ):
            with self.subTest(missing=missing):
                payload = json_mod.loads(
                    self._run(["--diff", D_A.isoformat(), D_B.isoformat(), "--json"], snaps)
                )
                self.assertEqual(payload["missing_dates"], [d.isoformat() for d in missing])
                self.assertEqual(payload["date_a"], D_A.isoformat())
                self.assertEqual(payload["date_b"], D_B.isoformat())
                self.assertEqual(payload["entries"], [])
                self.assertEqual(payload["exits"], [])
                self.assertEqual(payload["moves"], [])
                self.assertEqual(payload["unchanged"], 0)

    def test_bad_date_is_rejected(self) -> None:
        result = CliRunner().invoke(app, ["top-ranks", "--date", "nope"])
        self.assertNotEqual(result.exit_code, 0)

    def test_date_and_diff_are_exclusive(self) -> None:
        result = CliRunner().invoke(
            app, ["top-ranks", "--date", "2026-10-29", "--diff", "2026-10-29", "2027-01-08"]
        )
        self.assertNotEqual(result.exit_code, 0)


class _Cur:
    def __init__(self, store: dict, rows: list) -> None:
        self._store, self._rows = store, rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self._store["sql"] = sql
        self._store["params"] = list(params)

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, store: dict, rows: list) -> None:
        self._store, self._rows = store, rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cur(self._store, self._rows)


class FetchSnapshotsTests(unittest.TestCase):
    def test_groups_rows_by_date_and_coerces_decimals(self) -> None:
        from genkei.cli.top_ranks import _fetch_snapshots

        store: dict = {}
        rows = [
            (D_A, "bitcoin", "BTC", 1, Decimal("2e12"), Decimal("100000"), Decimal("5e10"), None),
            (D_B, "bitcoin", "BTC", 1, None, None, None, None),
        ]
        with patch("genkei.cli.top_ranks.db.connection", return_value=_Conn(store, rows)):
            out = _fetch_snapshots([D_A, D_B, date(2030, 1, 1)])
        self.assertIn("coingecko.market_rank", store["sql"])
        self.assertEqual(out[D_A][0]["market_cap_usd"], 2e12)
        self.assertIsNone(out[D_B][0]["market_cap_usd"])
        self.assertEqual(out[date(2030, 1, 1)], [])


if __name__ == "__main__":
    unittest.main()
