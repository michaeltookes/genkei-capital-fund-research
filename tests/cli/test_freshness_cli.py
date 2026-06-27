"""Command-level tests for the B-023 freshness warnings.

Verifies the read-path subcommands (prices / tvl / macro) warn on stderr
when data is stale, stay silent when fresh, and never alter the --json
stdout payload (the bare row list the reflection cycle parses).
"""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from genkei.cli import main


def _ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


class PricesFreshnessTests(unittest.TestCase):
    def _run(self, rows, args, *, latest_ts=None):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch(
                "genkei.cli.prices._query_coingecko_market_data", return_value=rows
            ),
            patch(
                "genkei.cli.prices._query_coingecko_latest_ts", return_value=latest_ts
            ) as latest,
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            main(["prices", "--ticker", "BTC", *args])
        return out.getvalue(), err.getvalue(), latest

    def _row(self, hours_ago: float):
        return {
            "ts": _ts(hours_ago),
            "price_usd": 65000.0,
            "market_cap_usd": None,
            "volume_usd": 1.0,
        }

    def test_fresh_emits_no_warning(self) -> None:
        out, err, _latest = self._run([self._row(2)], [])
        self.assertIn("65,000", out)
        self.assertNotIn("STALE", err)

    def test_stale_warns_on_stderr_not_stdout(self) -> None:
        out, err, _latest = self._run([self._row(100)], [])
        self.assertIn("STALE", err)
        self.assertNotIn("STALE", out)  # human banner is stderr-only

    def test_threshold_override_suppresses_warning(self) -> None:
        # 100h old, but a 200h threshold → silent.
        _out, err, _latest = self._run(
            [self._row(100)], ["--max-snapshot-age-hours", "200"]
        )
        self.assertNotIn("STALE", err)

    def test_json_stdout_stays_a_bare_list_when_stale(self) -> None:
        out, err, _latest = self._run([self._row(100)], ["--json"])
        parsed = json_mod.loads(out)
        # Contract: stdout is the bare row list, unwrapped.
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["price_usd"], 65000.0)
        # The structured freshness object rides on stderr.
        freshness = json_mod.loads(err)["freshness"]
        self.assertTrue(freshness["stale"])
        self.assertEqual(freshness["source"], "coingecko.market_data")

    def test_empty_result_emits_no_warning(self) -> None:
        _out, err, _latest = self._run([], [])
        self.assertNotIn("STALE", err)

    def test_historical_until_uses_unbounded_latest_row_for_freshness(self) -> None:
        _out, err, latest = self._run(
            [self._row(1000)],
            ["--until", "2024-01-31"],
            latest_ts=_ts(2),
        )
        latest.assert_called_once_with("bitcoin")
        self.assertNotIn("STALE", err)


class TvlFreshnessTests(unittest.TestCase):
    def test_stale_chain_tvl_warns_on_stderr(self) -> None:
        rows = [{"ts": _ts(100), "tvl_usd": 73_500_000_000}]
        out, err = io.StringIO(), io.StringIO()
        with (
            patch("genkei.cli.tvl._query_chain_tvl", return_value=rows),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            main(["tvl", "--chain", "Ethereum"])
        self.assertIn("STALE", err.getvalue())
        self.assertNotIn("STALE", out.getvalue())

    def test_protocol_tvl_warning_uses_protocol_source_label(self) -> None:
        rows = [{"ts": _ts(100), "chain": "Ethereum", "tvl_usd": 1_000_000}]
        out, err = io.StringIO(), io.StringIO()
        with (
            patch("genkei.cli.tvl._query_protocol_tvl", return_value=rows),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            main(["tvl", "--protocol", "aave-v3"])
        self.assertIn("STALE", err.getvalue())
        self.assertIn("defillama.protocol_tvl", err.getvalue())
        self.assertNotIn("defillama.chain_tvl", err.getvalue())


class MacroFreshnessTests(unittest.TestCase):
    """Macro judges freshness on the FRED ingest run, not observation ts."""

    def _run(self, freshness, rows):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch("genkei.cli.macro._query_observations", return_value=rows),
            patch(
                "genkei.cli.macro.ingest_run_freshness", return_value=freshness
            ) as mocked,
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            main(["macro", "--series", "DGS10"])
        return out.getvalue(), err.getvalue(), mocked

    def test_checks_fred_normalize_run(self) -> None:
        fresh = {
            "source": "fred/normalize",
            "last_ts": _ts(5),
            "age_hours": 5.0,
            "max_age_hours": 36.0,
            "stale": False,
            "kind": "ingest_run",
        }
        rows = [
            {
                "ts": "2026-06-25T00:00:00+00:00",
                "realtime_start": "2026-06-25",
                "realtime_end": "9999-12-31",
                "value": 4.5,
            }
        ]
        _out, err, mocked = self._run(fresh, rows)
        # Wired to the fred normalize run, not the observation date.
        self.assertEqual(mocked.call_args.args, ("fred", "normalize"))
        self.assertNotIn("STALE", err)

    def test_stale_fred_pipeline_warns(self) -> None:
        stale = {
            "source": "fred/normalize",
            "last_ts": _ts(80),
            "age_hours": 80.0,
            "max_age_hours": 36.0,
            "stale": True,
            "kind": "ingest_run",
        }
        rows = [
            {
                "ts": "2026-06-20T00:00:00+00:00",
                "realtime_start": "2026-06-20",
                "realtime_end": "9999-12-31",
                "value": 4.5,
            }
        ]
        _out, err, _mocked = self._run(stale, rows)
        self.assertIn("STALE", err)


if __name__ == "__main__":
    unittest.main()
