"""Unit tests for schema-drift detection (B-072).

The pure ``check_payload`` function is the unit under test. The
DB-touching ``check_recent_blobs`` is exercised at the CLI integration
layer (test_watchlist_cmd) so this module stays offline + deterministic.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from genkei.common.schema_drift import (
    NATURAL_KEY_SPECS,
    SCHEMA_SPECS,
    EndpointSchema,
    NaturalKeyUniquenessSpec,
    check_natural_key_uniqueness,
    check_payload,
    check_recent_blobs,
)


def _spec(
    *,
    payload_type: str = "object",
    required_keys: tuple[str, ...] = ("a", "b"),
    array_sample_size: int = 3,
    nested_paths: tuple[str, ...] = (),
    array_item_min_length: int | None = None,
) -> EndpointSchema:
    return EndpointSchema(
        source="testsrc",
        endpoint_kind="test_endpoint",
        endpoint_pattern="test\\_%",
        payload_type=payload_type,
        required_keys=required_keys,
        array_sample_size=array_sample_size,
        nested_paths=nested_paths,
        array_item_min_length=array_item_min_length,
    )


class ObjectPayloadTests(unittest.TestCase):
    def test_all_required_keys_present_yields_no_issues(self) -> None:
        spec = _spec(required_keys=("a", "b"))
        self.assertEqual(check_payload({"a": 1, "b": 2, "extra": 3}, spec), [])

    def test_one_missing_key_yields_one_issue(self) -> None:
        spec = _spec(required_keys=("a", "b"))
        issues = check_payload({"a": 1}, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "MISSING_REQUIRED_KEY")
        self.assertIn("'b'", issues[0].detail)

    def test_multiple_missing_keys_each_get_their_own_issue(self) -> None:
        spec = _spec(required_keys=("a", "b", "c"))
        issues = check_payload({"x": 1}, spec)
        self.assertEqual(len(issues), 3)
        kinds = {i.kind for i in issues}
        self.assertEqual(kinds, {"MISSING_REQUIRED_KEY"})
        missing = sorted(
            i.detail.split("'")[1] for i in issues if "'" in i.detail
        )
        self.assertEqual(missing, ["a", "b", "c"])

    def test_array_when_expecting_object_yields_type_mismatch(self) -> None:
        spec = _spec(payload_type="object", required_keys=("a",))
        issues = check_payload([1, 2, 3], spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "WRONG_TOP_LEVEL_TYPE")
        self.assertIn("expected object, got array", issues[0].detail)

    def test_null_when_expecting_object_yields_type_mismatch(self) -> None:
        spec = _spec(payload_type="object", required_keys=("a",))
        issues = check_payload(None, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "WRONG_TOP_LEVEL_TYPE")
        self.assertIn("got null", issues[0].detail)

    def test_nested_path_present(self) -> None:
        spec = _spec(
            required_keys=("market_data",),
            nested_paths=("market_data.current_price.usd",),
        )
        payload = {"market_data": {"current_price": {"usd": 50_000.0}}}
        self.assertEqual(check_payload(payload, spec), [])

    def test_nested_path_missing_at_leaf(self) -> None:
        spec = _spec(
            required_keys=("market_data",),
            nested_paths=("market_data.current_price.usd",),
        )
        payload = {"market_data": {"current_price": {}}}
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "MISSING_NESTED_PATH")
        self.assertIn("market_data.current_price.usd", issues[0].detail)

    def test_nested_path_with_non_dict_intermediate(self) -> None:
        # If an intermediate path segment is not a dict (it's a string,
        # array, etc.), the path is unresolvable — should report missing.
        spec = _spec(required_keys=("a",), nested_paths=("a.b",))
        payload = {"a": "not a dict"}
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "MISSING_NESTED_PATH")


class ArrayPayloadTests(unittest.TestCase):
    def test_array_with_required_keys_in_every_item(self) -> None:
        spec = _spec(payload_type="array", required_keys=("slug", "name"))
        payload = [{"slug": "x", "name": "X"}, {"slug": "y", "name": "Y"}]
        self.assertEqual(check_payload(payload, spec), [])

    def test_empty_array_yields_empty_array_issue(self) -> None:
        spec = _spec(payload_type="array", required_keys=("slug",))
        issues = check_payload([], spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "EMPTY_ARRAY")

    def test_object_when_expecting_array_yields_type_mismatch(self) -> None:
        spec = _spec(payload_type="array", required_keys=("a",))
        issues = check_payload({"a": 1}, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "WRONG_TOP_LEVEL_TYPE")
        self.assertIn("expected array, got object", issues[0].detail)

    def test_missing_key_in_some_items_is_reported_with_count(self) -> None:
        spec = _spec(payload_type="array", required_keys=("slug",))
        # 2 of 3 items missing the key
        payload = [{"slug": "x"}, {"name": "no_slug"}, {"other": True}]
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "MISSING_REQUIRED_KEY")
        self.assertIn("2/3", issues[0].detail)

    def test_sampling_only_first_n_items(self) -> None:
        # With array_sample_size=2, item 3 (which is missing a key) is
        # not inspected — should report no drift.
        spec = _spec(payload_type="array", required_keys=("slug",), array_sample_size=2)
        payload = [{"slug": "x"}, {"slug": "y"}, {"name": "no_slug"}]
        self.assertEqual(check_payload(payload, spec), [])

    def test_array_of_non_objects_yields_type_mismatch(self) -> None:
        spec = _spec(payload_type="array", required_keys=("slug",))
        payload = [1, 2, 3]
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "WRONG_TOP_LEVEL_TYPE")
        self.assertIn("none of the first", issues[0].detail)

    def test_mixed_object_and_non_object_items_report_type_mismatch(self) -> None:
        spec = _spec(payload_type="array", required_keys=("slug",))
        payload = [{"slug": "x"}, "not an object", {"slug": "y"}]
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "WRONG_TOP_LEVEL_TYPE")
        self.assertIn("1/3", issues[0].detail)


class ArrayOfArraysPayloadTests(unittest.TestCase):
    """Validates the Coinbase-shaped payload check (B-035 / B-072 extension).

    Coinbase candles return ``[[time, low, high, open, close, volume], ...]``
    — positional fields, no keys. The spec uses ``array_item_min_length``
    instead of ``required_keys`` so the check validates item shape
    without naming individual indices.
    """

    def test_well_formed_candles_yield_no_issues(self) -> None:
        spec = _spec(payload_type="array", required_keys=(), array_item_min_length=6)
        payload = [
            [1700000000, 100.0, 110.0, 105.0, 108.0, 1234.5],
            [1700086400, 108.0, 115.0, 109.0, 114.0, 2000.0],
        ]
        self.assertEqual(check_payload(payload, spec), [])

    def test_short_items_yield_missing_required_key_issue(self) -> None:
        spec = _spec(payload_type="array", required_keys=(), array_item_min_length=6)
        # All items have only 5 elements — Coinbase changed the column count.
        payload = [
            [1700000000, 100.0, 110.0, 105.0, 108.0],
            [1700086400, 108.0, 115.0, 109.0, 114.0],
        ]
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "MISSING_REQUIRED_KEY")
        self.assertIn("length < 6", issues[0].detail)

    def test_items_that_are_not_lists_yield_type_mismatch(self) -> None:
        # Coinbase started returning objects instead of arrays.
        spec = _spec(payload_type="array", required_keys=(), array_item_min_length=6)
        payload = [{"t": 1700000000, "c": 108.0}, {"t": 1700086400, "c": 114.0}]
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "WRONG_TOP_LEVEL_TYPE")
        self.assertIn("none of the first", issues[0].detail)

    def test_mixed_list_and_non_list_items_yield_type_mismatch(self) -> None:
        spec = _spec(payload_type="array", required_keys=(), array_item_min_length=6)
        payload = [
            [1700000000, 100.0, 110.0, 105.0, 108.0, 1234.5],
            {"t": 1700086400, "c": 114.0},
        ]
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "WRONG_TOP_LEVEL_TYPE")
        self.assertIn("1/2", issues[0].detail)

    def test_empty_array_still_flags_via_normal_array_check(self) -> None:
        # Empty arrays are reported by the outer array check regardless
        # of array_item_min_length.
        spec = _spec(payload_type="array", required_keys=(), array_item_min_length=6)
        issues = check_payload([], spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "EMPTY_ARRAY")


class SpecRegistryTests(unittest.TestCase):
    """Lightweight sanity tests on the SCHEMA_SPECS registry.

    These guard against accidental drift in the spec list itself — adding
    a new spec but forgetting to set a payload_type, or duplicating a
    spec entry.
    """

    def test_all_specs_have_required_fields(self) -> None:
        for spec in SCHEMA_SPECS:
            self.assertIn(spec.payload_type, ("object", "array"))
            self.assertTrue(spec.source, f"empty source on {spec.endpoint_kind}")
            self.assertTrue(spec.endpoint_kind, "empty endpoint_kind")
            self.assertTrue(spec.endpoint_pattern, f"empty pattern on {spec.endpoint_kind}")
            # Spec must catch *some* drift — either via required_keys
            # (object / array-of-objects) or via array_item_min_length
            # (array-of-arrays like Coinbase candles).
            self.assertTrue(
                spec.required_keys or spec.array_item_min_length is not None,
                f"{spec.endpoint_kind} has neither required_keys nor "
                "array_item_min_length — spec wouldn't catch any drift",
            )

    def test_endpoint_kinds_are_unique(self) -> None:
        kinds = [s.endpoint_kind for s in SCHEMA_SPECS]
        self.assertEqual(len(kinds), len(set(kinds)), f"duplicate endpoint_kind in {kinds}")

    def test_expected_sources_have_at_least_one_spec(self) -> None:
        sources = {s.source for s in SCHEMA_SPECS}
        # Pin coverage so adding a new ingester forces a corresponding spec.
        self.assertEqual(
            sources,
            {"defillama", "coingecko", "fred", "sec", "coinbase", "yahoo", "cftc"},
        )

    def test_sec_submissions_spec_excludes_history_pages(self) -> None:
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "submissions_<cik>")
        self.assertIn("submissions\\_history\\_%", spec.endpoint_pattern_excludes)


class RecentBlobQueryTests(unittest.TestCase):
    def test_exclude_patterns_are_bound_into_recent_blob_query(self) -> None:
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "submissions_<cik>")
        captured: dict[str, object] = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql: str, params: list[object]) -> None:
                captured["sql"] = sql
                captured["params"] = params

            def fetchone(self):
                return (
                    "submissions_0000320193",
                    {"cik": "0000320193", "name": "Apple Inc.", "filings": {}},
                    datetime.now(timezone.utc),
                )

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("genkei.common.schema_drift.db.connection", return_value=FakeConn()):
            issues = check_recent_blobs(max_age_hours=72, specs=(spec,))

        self.assertEqual(issues, [])
        self.assertIn("endpoint_name NOT LIKE", str(captured["sql"]))
        self.assertIn("submissions\\_history\\_%", captured["params"])

    def test_backfill_only_spec_omits_freshness_cutoff(self) -> None:
        # A backfill-only endpoint must NOT constrain the query by
        # fetched_at — its blobs legitimately age between backfills, so we
        # sample the latest of any age and only shape-check it.
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "stablecoin_<id>")
        self.assertTrue(spec.backfill_only)
        captured: dict[str, object] = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql: str, params: list[object]) -> None:
                captured["sql"] = sql
                captured["params"] = params

            def fetchone(self):
                return (
                    "stablecoin_99",
                    {
                        "symbol": "USDC",
                        "name": "USD Coin",
                        "pegType": "peggedUSD",
                        "chainBalances": {"Ethereum": {"tokens": []}},
                    },
                    datetime.now(timezone.utc),
                )

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("genkei.common.schema_drift.db.connection", return_value=FakeConn()):
            issues = check_recent_blobs(max_age_hours=72, specs=(spec,))

        self.assertEqual(issues, [])
        self.assertNotIn("fetched_at >=", str(captured["sql"]))

    def test_backfill_only_spec_with_no_blobs_is_silent(self) -> None:
        # Never-backfilled endpoint (no rows) is not drift — stay quiet
        # rather than firing NO_RECENT_SAMPLES.
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "stablecoin_<id>")

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql: str, params: list[object]) -> None:
                pass

            def fetchone(self):
                return None

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("genkei.common.schema_drift.db.connection", return_value=FakeConn()):
            issues = check_recent_blobs(max_age_hours=72, specs=(spec,))

        self.assertEqual(issues, [])


class NaturalKeyUniquenessTests(unittest.TestCase):
    """Pin the table-level canary that surfaces day-align regressions."""

    def _capture_with_fake_db(self, *, returns: list[tuple[int]]) -> tuple[list, list[str]]:
        sqls: list[str] = []
        idx = {"i": 0}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql: str, params: list[object]) -> None:
                sqls.append(sql)

            def fetchone(self):
                row = returns[idx["i"]]
                idx["i"] += 1
                return row

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

            def rollback(self) -> None:
                pass

        with patch("genkei.common.schema_drift.db.connection", return_value=FakeConn()):
            issues = check_natural_key_uniqueness()
        return issues, sqls

    def test_zero_dupes_returns_empty(self) -> None:
        # All three specs see 0 duplicate groups → no issues.
        issues, _ = self._capture_with_fake_db(returns=[(0,), (0,), (0,)])
        self.assertEqual(issues, [])

    def test_dupes_surface_as_duplicate_natural_key_issue(self) -> None:
        # stablecoins has 3 dupe groups; the other two clean.
        issues, _ = self._capture_with_fake_db(returns=[(3,), (0,), (0,)])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].source, "defillama")
        self.assertEqual(issues[0].endpoint_kind, "defillama.stablecoins")
        self.assertEqual(issues[0].kind, "DUPLICATE_NATURAL_KEY")
        self.assertIn("3 (asset_id + chain, ts::date) group(s)", issues[0].detail)
        self.assertIn("day-align contract broken", issues[0].detail)

    def test_checker_error_rolls_back_before_continuing(self) -> None:
        specs = (
            NaturalKeyUniquenessSpec(
                source="defillama",
                table="defillama.bad_table",
                natural_key_cols=("slug",),
            ),
            NaturalKeyUniquenessSpec(
                source="defillama",
                table="defillama.good_table",
                natural_key_cols=("slug",),
            ),
        )

        class FakeCursor:
            def __init__(self) -> None:
                self.calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql: str, params: list[object]) -> None:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("boom")

            def fetchone(self):
                return (0,)

        class FakeConn:
            def __init__(self) -> None:
                self.cursor_obj = FakeCursor()
                self.rollbacks = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return self.cursor_obj

            def rollback(self) -> None:
                self.rollbacks += 1

        conn = FakeConn()
        with patch("genkei.common.schema_drift.db.connection", return_value=conn):
            issues = check_natural_key_uniqueness(specs)

        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.cursor_obj.calls, 2)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "CHECKER_ERROR")
        self.assertEqual(issues[0].endpoint_kind, "defillama.bad_table")

    def test_query_uses_natural_key_columns(self) -> None:
        # The SQL must reference each spec's natural-key columns + ts::date.
        _, sqls = self._capture_with_fake_db(returns=[(0,), (0,), (0,)])
        self.assertEqual(len(sqls), 3)
        stables_sql = sqls[0]
        self.assertIn("asset_id, chain", stables_sql)
        self.assertIn("ts::date", stables_sql)
        self.assertIn("defillama.stablecoins", stables_sql)
        # protocol_tvl uses (slug, chain); protocol_fees uses (slug,).
        self.assertIn("slug, chain", sqls[1])
        self.assertIn("defillama.protocol_tvl", sqls[1])
        self.assertIn("defillama.protocol_fees", sqls[2])

    def test_natural_key_specs_cover_all_three_affected_tables(self) -> None:
        tables = {spec.table for spec in NATURAL_KEY_SPECS}
        self.assertEqual(
            tables,
            {
                "defillama.stablecoins",
                "defillama.protocol_tvl",
                "defillama.protocol_fees",
            },
        )

    def test_spec_dataclass_carries_natural_key_tuple(self) -> None:
        spec = NaturalKeyUniquenessSpec(
            source="test",
            table="test.tbl",
            natural_key_cols=("a", "b"),
        )
        self.assertEqual(spec.natural_key_cols, ("a", "b"))
        self.assertEqual(spec.lookback_days, 30)  # default


class RealisticPayloadShapeTests(unittest.TestCase):
    """Tests that use payload shapes close to what the live APIs return.

    Catches regressions where someone broadens a spec to fields the
    real API doesn't return, or where the spec accidentally requires a
    field that's actually optional. Reference shapes come from
    inspecting live raw_blobs (see commit message for the queries).
    """

    def test_defillama_protocols_canonical_shape(self) -> None:
        # Realistic /protocols payload: top-level array of protocol
        # objects with slug + name + tvl, plus a bunch of optional fields
        # we don't check (chains, gecko_id, symbol, methodology, etc.).
        payload = [
            {"slug": "aave-v3", "name": "Aave V3", "tvl": 11_000_000_000.0, "chains": ["Ethereum"]},
            {"slug": "lido", "name": "Lido", "tvl": 25_000_000_000.0, "chains": ["Ethereum"]},
        ]
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "protocols")
        self.assertEqual(check_payload(payload, spec), [])

    def test_coingecko_market_chart_three_parallel_arrays(self) -> None:
        # /market_chart returns prices, market_caps, total_volumes as
        # three parallel arrays of [ts_ms, value]. The normalizer zips
        # them by index; all three are load-bearing per G-024.
        payload = {
            "prices": [[1716000000000, 70_000.0]],
            "market_caps": [[1716000000000, 1_400_000_000_000.0]],
            "total_volumes": [[1716000000000, 40_000_000_000.0]],
        }
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "market_chart_<id>")
        self.assertEqual(check_payload(payload, spec), [])

    def test_coingecko_market_chart_missing_total_volumes_is_drift(self) -> None:
        payload = {
            "prices": [[1716000000000, 70_000.0]],
            "market_caps": [[1716000000000, 1_400_000_000_000.0]],
            # total_volumes missing — drift
        }
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "market_chart_<id>")
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "MISSING_REQUIRED_KEY")
        self.assertIn("total_volumes", issues[0].detail)

    def test_fred_series_uses_seriess_typo(self) -> None:
        # FRED's payload uses "seriess" (sic) — per G-018. The spec
        # encodes this; a future "fix" upstream would surface as drift.
        payload_correct = {"seriess": [{"id": "DGS10"}]}
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "series_<id>")
        self.assertEqual(check_payload(payload_correct, spec), [])

        # If FRED ever fixes the typo to "series", we'd see drift here.
        payload_drift = {"series": [{"id": "DGS10"}]}
        issues = check_payload(payload_drift, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "MISSING_REQUIRED_KEY")
        self.assertIn("seriess", issues[0].detail)

    def _stablecoin_spec(self):
        return next(s for s in SCHEMA_SPECS if s.endpoint_kind == "stablecoin_<id>")

    def _stablecoin_payload(self, chain_key: str) -> dict:
        eth_chain = {
            "tokens": [
                {"date": 1716000000, "circulating": {"peggedUSD": 50_000_000_000}},
            ],
        }
        return {
            "symbol": "USDC",
            "name": "USD Coin",
            "pegType": "peggedUSD",
            chain_key: {"Ethereum": eth_chain},
        }

    def test_defillama_stablecoin_id_accepts_chain_circulating_key(self) -> None:
        # Newer payloads carry the per-chain series under `chainCirculating`.
        spec = self._stablecoin_spec()
        self.assertEqual(check_payload(self._stablecoin_payload("chainCirculating"), spec), [])

    def test_defillama_stablecoin_id_accepts_chain_balances_key(self) -> None:
        # The live /stablecoin/{id} endpoint actually serves the series under
        # `chainBalances` (G-043); the normalizer accepts either, so the spec
        # must too rather than hard-requiring `chainCirculating`.
        spec = self._stablecoin_spec()
        self.assertEqual(check_payload(self._stablecoin_payload("chainBalances"), spec), [])

    def test_defillama_stablecoin_id_missing_both_chain_keys_is_drift(self) -> None:
        # If neither interchangeable key is present the normalizer silently
        # yields zero rows — exactly the drift the any_of_keys check guards.
        spec = self._stablecoin_spec()
        payload = {"symbol": "USDC", "name": "USD Coin", "pegType": "peggedUSD"}
        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "MISSING_ANY_OF_KEYS")

    def test_defillama_stablecoin_id_rejects_unparseable_chain_key_value(self) -> None:
        # A present key is not enough: normalize_stablecoin_history requires
        # the selected chain balance container to be a dict, otherwise it
        # silently returns zero rows.
        spec = self._stablecoin_spec()
        for bad_value in (None, []):
            with self.subTest(bad_value=bad_value):
                payload = {
                    "symbol": "USDC",
                    "name": "USD Coin",
                    "pegType": "peggedUSD",
                    "chainBalances": bad_value,
                }
                issues = check_payload(payload, spec)
                self.assertEqual(len(issues), 1)
                self.assertEqual(issues[0].kind, "MISSING_ANY_OF_KEYS")
                self.assertIn("expected object shape", issues[0].detail)

    def test_defillama_stablecoin_id_accepts_one_parseable_chain_key_value(self) -> None:
        spec = self._stablecoin_spec()
        payload = self._stablecoin_payload("chainCirculating")
        payload["chainBalances"] = []

        self.assertEqual(check_payload(payload, spec), [])

    def test_defillama_stablecoin_id_rejects_unparseable_preferred_chain_key(self) -> None:
        # The normalizer selects chainCirculating whenever it exists. A valid
        # fallback chainBalances value must not hide an unparseable preferred
        # value that would make the normalizer return zero rows.
        spec = self._stablecoin_spec()
        payload = self._stablecoin_payload("chainBalances")
        payload["chainCirculating"] = []

        issues = check_payload(payload, spec)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "MISSING_ANY_OF_KEYS")
        self.assertIn("expected object shape", issues[0].detail)

    def test_defillama_chain_tvl_history_array_of_dated_points(self) -> None:
        payload = [
            {"date": 1715000000, "tvl": 60_000_000_000.0},
            {"date": 1716000000, "tvl": 59_000_000_000.0},
        ]
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "chain_tvl_history_<chain>")
        self.assertEqual(check_payload(payload, spec), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
