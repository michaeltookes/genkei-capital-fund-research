"""Unit tests for schema-drift detection (B-072).

The pure ``check_payload`` function is the unit under test. The
DB-touching ``check_recent_blobs`` is exercised at the CLI integration
layer (test_watchlist_cmd) so this module stays offline + deterministic.
"""

from __future__ import annotations

import unittest

from genkei.common.schema_drift import (
    SCHEMA_SPECS,
    EndpointSchema,
    check_payload,
)


def _spec(
    *,
    payload_type: str = "object",
    required_keys: tuple[str, ...] = ("a", "b"),
    array_sample_size: int = 3,
    nested_paths: tuple[str, ...] = (),
) -> EndpointSchema:
    return EndpointSchema(
        source="testsrc",
        endpoint_kind="test_endpoint",
        endpoint_pattern="test\\_%",
        payload_type=payload_type,
        required_keys=required_keys,
        array_sample_size=array_sample_size,
        nested_paths=nested_paths,
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
            self.assertTrue(
                spec.required_keys,
                f"{spec.endpoint_kind} has no required_keys — spec wouldn't catch any drift",
            )

    def test_endpoint_kinds_are_unique(self) -> None:
        kinds = [s.endpoint_kind for s in SCHEMA_SPECS]
        self.assertEqual(len(kinds), len(set(kinds)), f"duplicate endpoint_kind in {kinds}")

    def test_expected_sources_have_at_least_one_spec(self) -> None:
        sources = {s.source for s in SCHEMA_SPECS}
        # Pin coverage so adding a new ingester forces a corresponding spec.
        self.assertEqual(sources, {"defillama", "coingecko", "fred", "sec"})


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

    def test_defillama_stablecoin_id_uses_chain_circulating_key(self) -> None:
        # G-005 lesson: the per-stablecoin endpoint uses
        # `chainCirculating`, NOT `chainBalances`. Spec encodes this.
        eth_chain = {
            "tokens": [
                {"date": 1716000000, "circulating": {"peggedUSD": 50_000_000_000}},
            ],
        }
        payload = {
            "symbol": "USDC",
            "name": "USD Coin",
            "pegType": "peggedUSD",
            "chainCirculating": {"Ethereum": eth_chain},
        }
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "stablecoin_<id>")
        self.assertEqual(check_payload(payload, spec), [])

    def test_defillama_chain_tvl_history_array_of_dated_points(self) -> None:
        payload = [
            {"date": 1715000000, "tvl": 60_000_000_000.0},
            {"date": 1716000000, "tvl": 59_000_000_000.0},
        ]
        spec = next(s for s in SCHEMA_SPECS if s.endpoint_kind == "chain_tvl_history_<chain>")
        self.assertEqual(check_payload(payload, spec), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
